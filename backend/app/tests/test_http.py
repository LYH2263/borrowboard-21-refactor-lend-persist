"""HTTP wiring tests: routes only orchestrate the commit service."""
from fastapi.testclient import TestClient

from app.main import app


def client():
    return TestClient(app)


def test_health_and_seeded_board_consistent(db_dir):
    with client() as c:
        assert c.get("/api/health").json() == {"ok": True, "project": "borrowboard"}
        b = c.get("/api/board").json()
    # Top strip numbers equal actual pane rows in the single response.
    assert b["counts"]["available"] == len(b["available"])
    assert b["counts"]["active"] == len(b["active"])
    assert b["counts"]["overdue"] == len(b["overdue"])
    # Seed data: items 1-3 available, item 4 on_loan and long overdue.
    assert {i["id"] for i in b["available"]} == {1, 2, 3}
    assert b["counts"]["overdue"] == 1 and b["overdue"][0]["item_id"] == 4


def test_lend_free_item_then_second_lend_rejected(db_dir):
    with client() as c:
        r1 = c.post("/api/items/1/lend", json={"borrower": "甲", "due_date": "2026-12-31"})
        assert r1.status_code == 200 and r1.json()["loan_id"] > 0
        r2 = c.post("/api/items/1/lend", json={"borrower": "乙", "due_date": "2026-12-31"})
        assert r2.status_code == 409
        # Whole rejected order wrote nothing new: one active loan, off shelves.
        b = c.get("/api/board").json()
        assert b["counts"]["active"] == 1
        assert all(i["id"] != 1 for i in b["available"])
        assert b["counts"]["available"] == len(b["available"])


def test_lend_unknown_item_404(db_dir):
    with client() as c:
        assert c.post("/api/items/4040/lend",
                      json={"borrower": "甲", "due_date": "2026-12-31"}).status_code == 404


def test_return_makes_returned_loan_not_overdue(db_dir):
    with client() as c:
        lid = c.post("/api/items/1/lend",
                     json={"borrower": "甲", "due_date": "2020-01-01"}).json()["loan_id"]
        assert c.post(f"/api/loans/{lid}/return").status_code == 200
        loans = c.get("/api/loans").json()
        # Returned despite a past due date -> not in overdue, back available.
        assert all(l["id"] != lid for l in loans["overdue"])
        assert any(l["id"] == lid for l in loans["returned"])
        b = c.get("/api/board").json()
        assert any(i["id"] == 1 for i in b["available"])
        assert b["counts"]["available"] == len(b["available"])


def test_return_twice_400(db_dir):
    with client() as c:
        lid = c.post("/api/items/1/lend",
                     json={"borrower": "甲", "due_date": "2026-12-31"}).json()["loan_id"]
        c.post(f"/api/loans/{lid}/return")
        assert c.post(f"/api/loans/{lid}/return").status_code == 400
