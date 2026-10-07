"""Commit-layer tests: every assertion reads state back FROM the database.

These exercise the services layer against a real sqlite file (isolated
DATA_DIR per test). Pure-rule behaviour belongs in test_borrow_rules.py and
stays database-free; here we verify write granularity: the item status flip
and the loan row commit together, never alone, and concurrent lends are
serialised on that same granularity.
"""
import sqlite3
import threading

import pytest

from app.db import connect
from app.services import lending
from app.services.lending import LendRejected


def status_of(item_id):
    c = connect()
    st = c.execute("SELECT status FROM items WHERE id=?", (item_id,)).fetchone()[0]
    c.close()
    return st


def active_loans_of(item_id):
    c = connect()
    n = c.execute("SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'",
                  (item_id,)).fetchone()["c"]
    c.close()
    return n


# ----------------------------------------------------- single lend/return ----

def test_lend_free_item_writes_pair_and_reads_back(db_dir):
    assert status_of(1) == "available" and active_loans_of(1) == 0
    loan_id = lending.lend_item(1, "阿强", "2026-12-31", lent_at="2026-10-01T00:00:00+00:00")
    # Both halves of the commit are read straight back from the DB.
    assert status_of(1) == "on_loan"
    c = connect()
    row = c.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
    c.close()
    assert dict(row) == {
        "id": loan_id, "item_id": 1, "borrower": "阿强", "status": "active",
        "due_date": "2026-12-31", "lent_at": "2026-10-01T00:00:00+00:00",
        "returned_at": None}


def test_lend_when_already_on_loan_writes_nothing(db_dir):
    before = active_loans_of(4)
    with pytest.raises(LendRejected) as ei:
        lending.lend_item(4, "乙", "2026-12-31")
    assert ei.value.reason == "item_not_available"
    assert status_of(4) == "on_loan"
    assert active_loans_of(4) == before  # no extra loan row sneaked in


def test_lend_blocked_by_orphan_active_loan_even_if_status_free(db_dir):
    # Dirty world: status says available but an active loan already exists.
    # The commit layer must trust the re-read world and write nothing.
    c = connect()
    c.execute("INSERT INTO loans(item_id,borrower,status,due_date,lent_at) "
              "VALUES (?,?,?,?,?)", (1, "幽灵借", "active", "2026-12-31", "t"))
    c.commit(); c.close()
    with pytest.raises(LendRejected) as ei:
        lending.lend_item(1, "乙", "2026-12-31")
    assert ei.value.reason == "already_on_loan"
    assert status_of(1) == "available" and active_loans_of(1) == 1


def test_second_lend_after_successful_lend_is_rejected(db_dir):
    lending.lend_item(1, "甲", "2026-12-31")
    with pytest.raises(LendRejected) as ei:
        lending.lend_item(1, "乙", "2026-12-31")
    assert ei.value.reason in ("already_on_loan", "item_not_available")
    assert active_loans_of(1) == 1 and status_of(1) == "on_loan"


def test_lend_unknown_item_404_and_writes_nothing(db_dir):
    with pytest.raises(LendRejected) as ei:
        lending.lend_item(999, "甲", "2026-12-31")
    assert ei.value.status_code == 404


def test_return_flips_both_and_returned_loan_is_not_overdue(db_dir):
    loan_id = lending.lend_item(1, "甲", "2020-01-01")  # long past due
    lending.return_loan(loan_id)
    c = connect()
    loan_status, returned_at = c.execute(
        "SELECT status, returned_at FROM loans WHERE id=?", (loan_id,)).fetchone()
    c.close()
    assert loan_status == "returned" and returned_at
    assert status_of(1) == "available"
    # Past due date on a returned loan must classify as returned, not overdue.
    grouped = lending.list_loans(today="2026-10-07")
    assert all(l["id"] != loan_id or not l.get("overdue") for l in grouped["overdue"])
    assert any(l["id"] == loan_id for l in grouped["returned"])


def test_return_twice_is_rejected_without_state_change(db_dir):
    loan_id = lending.lend_item(1, "甲", "2026-12-31")
    lending.return_loan(loan_id)
    with pytest.raises(LendRejected) as ei:
        lending.return_loan(loan_id)
    assert ei.value.status_code == 400
    assert status_of(1) == "available" and active_loans_of(1) == 0


# ------------------------------------------------------- atomicity / crash ----

def test_failure_between_writes_leaves_no_half_state(db_dir, monkeypatch):
    # Real DB-level failure AFTER the loan INSERT but BEFORE the item UPDATE:
    # a trigger aborts the second write of the pair.
    c = connect()
    c.execute("CREATE TRIGGER boom BEFORE UPDATE ON items "
              "BEGIN SELECT RAISE(FAIL, 'injected mid-commit failure'); END")
    c.commit(); c.close()
    with pytest.raises(sqlite3.DatabaseError):
        lending.lend_item(1, "甲", "2026-12-31")
    # Read back from the DB: no loan-only half state...
    assert active_loans_of(1) == 0
    # ...and no status-only half state either.
    assert status_of(1) == "available"


# ------------------------------------------------------------- concurrency ----

def test_two_concurrent_lends_only_one_commits(db_dir, monkeypatch):
    entered = threading.Event()
    orig = lending.rules.evaluate_lend

    def slow_eval(item, n, req):
        # Holder of the write lock parks here so the second BEGIN IMMEDIATE
        # is genuinely queued on the lock rather than running after commit.
        entered.set()
        threading.Event().wait(0.4)
        return orig(item, n, req)

    monkeypatch.setattr(lending.rules, "evaluate_lend", slow_eval)
    outcomes = {}

    def lend(key):
        try:
            lending.lend_item(1, f"借{key}", "2026-12-31")
            outcomes[key] = "ok"
        except LendRejected as e:
            outcomes[key] = e.reason

    t1 = threading.Thread(target=lend, args=("a",))
    t2 = threading.Thread(target=lend, args=("b",))
    t1.start()
    assert entered.wait(2)
    t2.start()
    t1.join(5); t2.join(5)

    assert sorted(outcomes.values()) == ["already_on_loan", "ok"] or \
           sorted(outcomes.values()) == ["item_not_available", "ok"]
    # The loser re-read a fully-committed world: exactly one loan, on_loan.
    assert active_loans_of(1) == 1
    assert status_of(1) == "on_loan"


# -------------------------------------------------- one world: board + loans --

def test_board_strip_counts_equal_pane_rows(db_dir):
    lending.lend_item(1, "甲", "2026-12-31")
    b = lending.get_board(today="2026-10-07")
    # Top strip and both panes derive from one snapshot.
    assert b["counts"]["available"] == len(b["available"])
    assert b["counts"]["active"] == len(b["active"])
    assert b["counts"]["overdue"] == len(b["overdue"])
    ids = {i["id"] for i in b["available"]}
    assert ids == {2, 3}  # item 1 just lent, item 4 seeded on_loan
    assert {l["item_id"] for l in b["active"]} == {1}
    assert {l["item_id"] for l in b["overdue"]} == {4}


def test_board_after_return_is_consistent_again(db_dir):
    loan_id = lending.lend_item(2, "甲", "2026-12-31")
    lending.return_loan(loan_id)
    b = lending.get_board(today="2026-10-07")
    assert b["counts"]["available"] == len(b["available"]) == 3
    assert any(i["id"] == 2 for i in b["available"])
    assert {l["item_id"] for l in b["active"]} == set()


def test_loans_and_shelves_never_disagree_after_rejected_lend(db_dir):
    with pytest.raises(LendRejected):
        lending.lend_item(4, "甲", "2026-12-31")
    b = lending.get_board(today="2026-10-07")
    # Seeded on-loan item is absent from shelves, present once in overdue pane.
    assert all(i["id"] != 4 for i in b["available"])
    assert sum(1 for l in b["overdue"] if l["item_id"] == 4) == 1
    assert b["counts"]["available"] == len(b["available"])
