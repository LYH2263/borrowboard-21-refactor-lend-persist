"""Commit layer: snapshots rows, runs pure rules, writes back atomically.

Only this layer mutates the database. Flow is always the same:

    snapshot (inside one transaction) -> pure calculus in engines ->
    paired writes -> single commit

The pure functions decide *what* may be written; this layer decides the
*granularity*: item status and loan row are committed in the same
``BEGIN IMMEDIATE`` transaction, so a board/loans reader can never observe
one without the other, and a second concurrent lend re-reads the world only
after the first one has fully committed.
"""

from datetime import date, datetime, timezone

from app.db import read_transaction, write_transaction
from app.engines import borrow_rules as rules


class LendRejected(Exception):
    def __init__(self, reason: str, status_code: int = 409):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- reads ----

def get_board(today: str | None = None) -> dict:
    """Available pane, loan panes and the top strip from one snapshot."""
    today = today or date.today().isoformat()
    with read_transaction() as c:
        items = [dict(r) for r in c.execute("SELECT * FROM items")]
        loans = [dict(r) for r in c.execute(
            "SELECT loans.*, items.title FROM loans JOIN items ON items.id=loans.item_id")]
    return rules.build_board(items, loans, today)


def list_loans(today: str | None = None) -> dict:
    today = today or date.today().isoformat()
    with read_transaction() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT loans.*, items.title FROM loans JOIN items ON items.id=loans.item_id "
            "ORDER BY loans.id DESC")]
    return rules.classify_loans(rows, today)


# --------------------------------------------------------------- writes ----

def lend_item(item_id: int, borrower: str, due_date: str, lent_at: str | None = None) -> int:
    """Lend one item. Returns the new loan id.

    Reads the item and its active-loan count inside the same serialised
    transaction in which the paired writes happen. When the pure calculus
    rejects, nothing at all is written. When it passes, the loan insert and
    the ``items.status='on_loan'`` flip are one commit — a failure between
    them rolls both back, so no half state ever reaches the shelves, the
    board strip or the loans list.
    """
    lent_at = lent_at or _now()
    with write_transaction() as c:
        item = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if item is None:
            raise LendRejected("item", status_code=404)
        active_loans = c.execute(
            "SELECT COUNT(*) AS c FROM loans WHERE item_id=? AND status='active'",
            (item_id,)).fetchone()["c"]
        decision = rules.evaluate_lend(
            dict(item), active_loans, {"borrower": borrower, "due_date": due_date})
        if not decision["ok"]:
            # Rejected: nothing is written; rollback releases the lock.
            raise LendRejected(decision["reason"], status_code=409)
        cur = c.execute(
            "INSERT INTO loans(item_id,borrower,status,due_date,lent_at) VALUES (?,?,?,?,?)",
            (item_id, borrower, rules.ACTIVE, due_date, lent_at))
        loan_id = cur.lastrowid
        c.execute("UPDATE items SET status=? WHERE id=?", (rules.ON_LOAN, item_id))
        return loan_id  # commit happens when the context exits cleanly


def return_loan(loan_id: int, returned_at: str | None = None) -> None:
    """Return one loan: loan row and item status flip, single commit."""
    returned_at = returned_at or _now()
    with write_transaction() as c:
        loan = c.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
        if loan is None:
            raise LendRejected("loan", status_code=404)
        if loan["status"] != rules.ACTIVE:
            raise LendRejected("not_active", status_code=400)
        c.execute("UPDATE loans SET status=?, returned_at=? WHERE id=?",
                  (rules.RETURNED, returned_at, loan_id))
        c.execute("UPDATE items SET status=? WHERE id=?",
                  (rules.AVAILABLE, loan["item_id"]))


def add_item(title: str, owner: str) -> int:
    with write_transaction() as c:
        cur = c.execute(
            "INSERT INTO items(title,owner,status,data_quality) VALUES (?,?,?,?)",
            (title, owner, rules.AVAILABLE, "clean"))
        return cur.lastrowid


def list_items() -> list[dict]:
    with read_transaction() as c:
        return [dict(r) for r in c.execute("SELECT * FROM items")]


def settings() -> dict:
    with read_transaction() as c:
        return {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}
