"""HTTP wiring tests: routes only orchestrate the commit layer."""

from fastapi.testclient import TestClient

from app.main import app


def _client():
    # __enter__ fires the startup hook (seed) with DATA_DIR already pointed
    # at the per-test database by the db_dir fixture.
    return TestClient(app)


def test_health(db_dir):
    with _client() as c:
        r = c.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_lend_then_conflict_then_return_flow(db_dir):
    with _client() as c:
        # 空闲且无在借 -> 借出通过（前端唯一打的借出接口）
        r = c.post("/api/items/1/lend", json={"borrower": "邻居乙", "due_date": "2026-12-31"})
        assert r.status_code == 200
        lid = r.json()["loan_id"]

        board = c.get("/api/board").json()
        # 顶细条可借数 == 可借栏条数；在借同理
        assert board["counts"]["available"] == len(board["available"])
        assert board["counts"]["active"] == len(board["active"])
        assert 1 not in {i["id"] for i in board["available"]}

        # 已有在借 -> 再借被拒（409），看板世界不变
        again = c.post("/api/items/1/lend", json={"borrower": "路人", "due_date": "2026-12-31"})
        assert again.status_code == 409
        board2 = c.get("/api/board").json()
        assert board2["counts"] == board["counts"]

        # 归还
        ok = c.post(f"/api/loans/{lid}/return", json={})
        assert ok.status_code == 200
        board3 = c.get("/api/board").json()
        assert 1 in {i["id"] for i in board3["available"]}

        loans = c.get("/api/loans").json()
        returned_ids = {l["id"] for l in loans["returned"]}
        assert lid in returned_ids
        assert lid not in {l["id"] for l in loans["overdue"]}  # 已还不算逾期

        # 已还再还 -> 400
        assert c.post(f"/api/loans/{lid}/return", json={}).status_code == 400


def test_lend_seeded_on_loan_item_rejected(db_dir):
    with _client() as c:
        r = c.post("/api/items/4/lend", json={"borrower": "路人", "due_date": "2026-12-31"})
        assert r.status_code == 409


def test_lend_unknown_item_404(db_dir):
    with _client() as c:
        r = c.post("/api/items/999/lend", json={"borrower": "路人", "due_date": "2026-12-31"})
        assert r.status_code == 404


def test_seed_overdue_loan_classified_overdue_until_returned(db_dir):
    with _client() as c:
        board = c.get("/api/board").json()
        # 种子在借样例应还 2020-06-01，今天 2026 -> 逾期
        assert board["counts"]["overdue"] == 1
        assert board["overdue"][0]["item_id"] == 4
        c.post("/api/loans/1/return", json={})
        after = c.get("/api/loans").json()
        # 归还后落入 returned，逾期清零
        assert after["overdue"] == []
        assert any(l["id"] == 1 for l in after["returned"])
