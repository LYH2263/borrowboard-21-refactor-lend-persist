"""Pure-rule tests: no database, no app.db import, no DATA_DIR."""
import ast
import inspect
from pathlib import Path

from app.engines import borrow_rules
from app.engines.borrow_rules import (
    can_lend, is_overdue, classify_loans, evaluate_lend, build_board,
)

def test_module_has_no_database_dependency():
    # Pure by construction: the rules file may not import sqlite/app.db at all.
    tree = ast.parse(Path(inspect.getfile(borrow_rules)).read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    assert not any("sqlite" in m or m.startswith("app.db") for m in imports)

def test_mutex():
    assert can_lend("available", 0)["ok"]
    assert can_lend("available", 1)["reason"] == "already_on_loan"
    assert can_lend("retired", 0)["ok"] is False

def test_overdue():
    assert is_overdue("2020-01-01", "2026-01-01", "active")
    assert not is_overdue("2020-01-01", "2026-01-01", "returned")

def test_classify():
    r = classify_loans([
        {"id": 1, "status": "active", "due_date": "2020-01-01"},
        {"id": 2, "status": "active", "due_date": "2099-01-01"},
        {"id": 3, "status": "returned", "due_date": "2020-01-01"},
    ], "2026-01-01")
    assert len(r["overdue"]) == 1 and len(r["active"]) == 1 and len(r["returned"]) == 1

def test_evaluate_lend_accept_carries_exact_loan():
    res = evaluate_lend(
        {"id": 7, "status": "available"}, 0,
        {"borrower": "阿强", "due_date": "2026-12-31"})
    assert res["ok"]
    assert res["loan"] == {
        "item_id": 7, "borrower": "阿强", "status": "active", "due_date": "2026-12-31"}

def test_evaluate_lend_rejects_carry_nothing_writable():
    for item, n in [({"id": 1, "status": "on_loan"}, 1),
                    ({"id": 2, "status": "available"}, 1),
                    ({"id": 3, "status": "retired"}, 0)]:
        res = evaluate_lend(item, n, {"borrower": "x", "due_date": "2026-12-31"})
        assert not res["ok"] and "loan" not in res

def test_returned_with_past_due_is_never_overdue():
    assert is_overdue("2020-01-01", "2026-01-01", "returned") is False
    r = classify_loans([{"id": 9, "status": "returned", "due_date": "2020-01-01"}],
                       "2026-01-01")
    assert r == {"active": [], "overdue": [], "returned": [
        {"id": 9, "status": "returned", "due_date": "2020-01-01"}]}

def test_build_board_strip_equals_pane_lengths():
    board = build_board(
        [{"id": 1, "status": "available"},
         {"id": 2, "status": "on_loan"},
         {"id": 3, "status": "available"}],
        [{"id": 10, "item_id": 2, "status": "active", "due_date": "2020-01-01", "title": "钻"}],
        "2026-01-01")
    assert board["counts"]["available"] == len(board["available"]) == 2
    assert board["counts"]["overdue"] == len(board["overdue"]) == 1
    assert board["counts"]["active"] == len(board["active"]) == 0
    assert [i["id"] for i in board["available"]] == [1, 3]
