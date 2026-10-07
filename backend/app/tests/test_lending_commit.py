"""Commit-layer tests.

Unlike the pure-rule tests, these DO use the database - but every assertion
reads state back through a *fresh connection*, never through the connection
the service committed with.
"""

import threading

import pytest

import app.modules.lending as lending_mod
from app.db import connect
from app.modules import lending
from app.modules.lending import LendingError


def _item_status(item_id: int) -> str:
    c = connect()
    try:
        return c.execute("SELECT status FROM items WHERE id=?", (item_id,)).fetchone()["status"]
    finally:
        c.close()


def _active_loan_count(item_id: int) -> int:
    c = connect()
    try:
        return c.execute(
            "SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (item_id,)
        ).fetchone()["c"]
    finally:
        c.close()


def test_lend_committed_state_read_back(db_dir):
    # 空闲且无在借 -> 放出；从库里读回 status 与 loan 行。
    loan_id = lending.lend_item(1, "邻居乙", "2026-12-31", now="2026-10-07T00:00:00+00:00")
    assert _item_status(1) == "on_loan"
    c = connect()
    try:
        row = c.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
    finally:
        c.close()
    assert dict(row) == {
        "id": loan_id, "item_id": 1, "borrower": "邻居乙", "status": "active",
        "due_date": "2026-12-31", "lent_at": "2026-10-07T00:00:00+00:00",
        "returned_at": None,
    }


def test_lend_rejected_when_already_on_loan(db_dir):
    # 种子里 item 4 已在借 -> 整单不写。
    with pytest.raises(LendingError) as exc:
        lending.lend_item(4, "路人", "2026-12-31")
    assert exc.value.status_code == 409
    # 读回：仍然只有原来那一笔在借，status 未被触碰。
    assert _item_status(4) == "on_loan"
    assert _active_loan_count(4) == 1


def test_lend_unknown_item_writes_nothing(db_dir):
    with pytest.raises(LendingError) as exc:
        lending.lend_item(999, "路人", "2026-12-31")
    assert exc.value.status_code == 404
    c = connect()
    try:
        assert c.execute("SELECT COUNT(*) c FROM loans").fetchone()["c"] == 1  # 仅种子那笔
    finally:
        c.close()


def test_failure_between_insert_and_update_leaves_no_half_state(db_dir, monkeypatch):
    # 在 loans INSERT 与 items UPDATE 之间人为炸掉：
    # 不允许出现"只插了 loan"或"只改了 on_loan"的残局。
    real_connect = connect

    class ExplodingConnection:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, params=()):
            if sql.startswith("UPDATE items"):
                raise RuntimeError("simulated crash before status write-back")
            return self._inner.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(
        lending_mod, "connect", lambda: ExplodingConnection(real_connect())
    )
    with pytest.raises(RuntimeError):
        lending.lend_item(1, "邻居乙", "2026-12-31")

    # 用独立连接读回：loan 插入与 status 改动必须一起消失（ROLLBACK）。
    assert _item_status(1) == "available"
    assert _active_loan_count(1) == 0
    c = connect()
    try:
        assert c.execute("SELECT COUNT(*) c FROM loans WHERE item_id=1").fetchone()["c"] == 0
    finally:
        c.close()


def test_concurrent_lends_exactly_one_winner(db_dir):
    # 第二笔同时打来的借出读到的状态必须与提交粒度一致：一个赢家、一笔在借。
    for item_id in (1, 2):
        errors, winners = [], []
        barrier = threading.Barrier(2)

        def borrow(who):
            barrier.wait()
            try:
                lid = lending.lend_item(item_id, who, "2026-12-31")
                winners.append(lid)
            except LendingError as e:
                errors.append(e)

        t1 = threading.Thread(target=borrow, args=("甲",))
        t2 = threading.Thread(target=borrow, args=("乙",))
        t1.start(); t2.start(); t1.join(); t2.join()

        assert len(winners) == 1, errors
        assert len(errors) == 1 and errors[0].status_code == 409
        assert _item_status(item_id) == "on_loan"
        assert _active_loan_count(item_id) == 1


def test_return_atomic_and_idempotent_guard(db_dir):
    # 种子 loan 1 = item 4 的在借。
    lending.return_loan(1, now="2026-10-07T00:00:00+00:00")
    c = connect()
    try:
        loan = c.execute("SELECT status, returned_at FROM loans WHERE id=1").fetchone()
    finally:
        c.close()
    assert loan["status"] == "returned" and loan["returned_at"] == "2026-10-07T00:00:00+00:00"
    assert _item_status(4) == "available"
    # 已还再还 -> 400，且不产生任何写入。
    with pytest.raises(LendingError) as exc:
        lending.return_loan(1)
    assert exc.value.status_code == 400
    assert _item_status(4) == "available"


def test_board_reads_one_consistent_world(db_dir):
    # 借出 item 1 后：顶细条计数 == 栏内条数；在借栏里的物品不在可借栏。
    lending.lend_item(1, "邻居乙", "2026-12-31")
    board = lending.get_board("2026-10-07")

    assert board["counts"]["available"] == len(board["available"])
    assert board["counts"]["active"] == len(board["active"])
    assert board["counts"]["overdue"] == len(board["overdue"])

    available_ids = {i["id"] for i in board["available"]}
    loan_item_ids = {l["item_id"] for l in board["active"] + board["overdue"]}
    assert not (available_ids & loan_item_ids), "可借栏与在借栏出现同一物品"

    # 与数据库独立读回对账：同一世界。
    c = connect()
    try:
        db_available = c.execute(
            "SELECT COUNT(*) c FROM items WHERE status='available'"
        ).fetchone()["c"]
        db_active = c.execute(
            "SELECT COUNT(*) c FROM loans WHERE status='active'"
        ).fetchone()["c"]
        db_overdue = c.execute(
            "SELECT COUNT(*) c FROM loans WHERE status='active' AND due_date < ?",
            ("2026-10-07",),
        ).fetchone()["c"]
    finally:
        c.close()
    assert board["counts"]["available"] == db_available
    assert board["counts"]["active"] + board["counts"]["overdue"] == db_active
    assert board["counts"]["overdue"] == db_overdue


def test_board_stays_consistent_under_concurrent_writes(db_dir):
    # 边借边读：任何时刻读到的看板都必须自洽（计数=条数、左右不重叠）。
    violations = []

    def reader():
        for _ in range(50):
            b = lending.get_board("2026-10-07")
            if b["counts"] != {
                "available": len(b["available"]),
                "active": len(b["active"]),
                "overdue": len(b["overdue"]),
            }:
                violations.append(("counts", b["counts"]))
            overlap = {i["id"] for i in b["available"]} & {
                l["item_id"] for l in b["active"] + b["overdue"]
            }
            if overlap:
                violations.append(("overlap", overlap))

    def writer():
        for iid in (1, 2, 3):
            try:
                lending.lend_item(iid, "邻居", "2026-12-31")
            except LendingError:
                pass

    tr = threading.Thread(target=reader)
    tw = threading.Thread(target=writer)
    tr.start(); tw.start(); tr.join(); tw.join()
    assert violations == []
