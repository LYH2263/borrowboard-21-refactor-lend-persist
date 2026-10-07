"""Pure-rule tests. These MUST stay database-free: no app.db import, no I/O."""

from app.engines import borrow_rules as rules
from app.engines.borrow_rules import can_lend, can_return, classify_loans, is_overdue


def test_mutex():
    # 空闲且无在借 -> 可放出
    assert can_lend("available", 0) == {"ok": True, "reason": ""}
    # 已有在借 -> 拒
    assert can_lend("available", 1)["reason"] == "already_on_loan"
    assert can_lend("available", 2)["reason"] == "already_on_loan"
    # status 不是 available -> 拒（即使计数对不上，status 优先）
    assert can_lend("retired", 0)["ok"] is False
    assert can_lend("on_loan", 0)["reason"] == "item_not_available"


def test_return_eligibility():
    assert can_return("active") is True
    assert can_return("returned") is False


def test_overdue():
    # 在借且应还日早于今天 -> 逾期
    assert is_overdue("2020-01-01", "2026-01-01", "active")
    # 已还不算逾期，哪怕应还日早已过去
    assert not is_overdue("2020-01-01", "2026-01-01", "returned")
    # 应还日恰好是今天 -> 尚未逾期（严格早于）
    assert not is_overdue("2026-01-01", "2026-01-01", "active")
    # 没有应还日 -> 无法判逾期
    assert not is_overdue("", "2026-01-01", "active")
    assert not is_overdue(None, "2026-01-01", "active")
    # 未来应还日
    assert not is_overdue("2099-01-01", "2026-01-01", "active")


def test_classify():
    r = classify_loans([
        {"id": 1, "status": "active", "due_date": "2020-01-01"},
        {"id": 2, "status": "active", "due_date": "2099-01-01"},
        {"id": 3, "status": "returned", "due_date": "2020-01-01"},
    ], "2026-01-01")
    assert len(r["overdue"]) == 1 and len(r["active"]) == 1 and len(r["returned"]) == 1
    assert r["overdue"][0]["id"] == 1 and r["overdue"][0]["overdue"] is True
    assert r["active"][0]["id"] == 2 and r["active"][0]["overdue"] is False
    # 已还（含旧应还日）只进 returned
    assert r["returned"][0]["id"] == 3 and "overdue" not in r["returned"][0]


def test_classify_edge_cases():
    r = classify_loans([
        {"id": 1, "status": "active", "due_date": ""},
        {"id": 2, "status": "active", "due_date": "2026-01-01"},
    ], "2026-01-01")
    assert len(r["active"]) == 2 and r["overdue"] == []
    assert classify_loans([], "2026-01-01") == {"active": [], "overdue": [], "returned": []}


def test_pure_module_does_not_touch_database():
    # 规则层不允许引入任何持久化设施。
    import inspect
    src = inspect.getsource(rules)
    assert "connect" not in src
    assert "sqlite" not in src
