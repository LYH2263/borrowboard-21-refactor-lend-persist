"""Persistence / commit layer for lending.

The pure rules live in ``app.engines.borrow_rules`` and never touch a
database. Everything here is about *where the write-back granularity is
pinned*:

* A lend is ONE ``BEGIN IMMEDIATE`` transaction: the eligibility read, the
  ``loans`` INSERT and the ``items.status`` UPDATE all commit together or
  roll back together. A failure halfway through can never leave an item
  ``on_loan`` without a loan row, or a loan row without ``on_loan``.
* Because the lock is taken at the start of the transaction, a second
  concurrent lend re-reads the state the winning transaction committed - it
  can never lend against a stale ``available`` status.
* Board reads run inside a single deferred read transaction, so the left
  pane (available items), the right pane (active/overdue loans) and the top
  strip all observe one consistent snapshot - never a half-committed world.

HTTP routes only call these functions; they contain no SQL themselves.
"""

from datetime import datetime, timezone

from app.db import connect
from app.engines import borrow_rules as rules


class LendingError(Exception):
    """Service-level failure carrying the HTTP status the route should emit."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def lend_item(item_id: int, borrower: str, due_date: str, now: str | None = None) -> int:
    """Commit one loan. Returns the new loan id.

    Raises LendingError(404) / LendingError(409). On any failure the whole
    transaction is rolled back - no partial write reaches the database.
    """
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")  # write lock taken BEFORE the verdict read
        try:
            row = c.execute("SELECT status FROM items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                raise LendingError(404, "item")
            active_loans = c.execute(
                "SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'",
                (item_id,),
            ).fetchone()["c"]
            verdict = rules.can_lend(row["status"], active_loans)
            if not verdict["ok"]:
                # Mutex blocked: nothing has been written; roll back the
                # (empty) transaction and reject the whole request.
                raise LendingError(409, verdict["reason"])
            cur = c.execute(
                "INSERT INTO loans(item_id,borrower,status,due_date,lent_at)"
                " VALUES (?,?,?,?,?)",
                (item_id, borrower, rules.ACTIVE, due_date, now or _now()),
            )
            loan_id = cur.lastrowid
            c.execute(
                "UPDATE items SET status=? WHERE id=?", (rules.ON_LOAN, item_id)
            )
            c.execute("COMMIT")
            return loan_id
        except Exception:
            c.execute("ROLLBACK")
            raise
    finally:
        c.close()


def return_loan(loan_id: int, now: str | None = None) -> None:
    """Commit a return: loan -> returned and item -> available atomically."""
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            loan = c.execute(
                "SELECT item_id, status FROM loans WHERE id=?", (loan_id,)
            ).fetchone()
            if loan is None:
                raise LendingError(404, "loan")
            if not rules.can_return(loan["status"]):
                raise LendingError(400, "not_active")
            c.execute(
                "UPDATE loans SET status='returned', returned_at=? WHERE id=?",
                (now or _now(), loan_id),
            )
            c.execute(
                "UPDATE items SET status='available' WHERE id=?", (loan["item_id"],)
            )
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    finally:
        c.close()


def get_board(today: str) -> dict:
    """One-snapshot view feeding both split panes and the top strip."""
    c = connect()
    try:
        c.execute("BEGIN")  # deferred read transaction: all reads share a snapshot
        try:
            available = [
                dict(r) for r in c.execute("SELECT * FROM items WHERE status='available'")
            ]
            loans = [
                dict(r)
                for r in c.execute(
                    """SELECT loans.*, items.title FROM loans
                       JOIN items ON items.id=loans.item_id
                       WHERE loans.status='active'"""
                )
            ]
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    finally:
        c.close()
    classified = rules.classify_loans(loans, today)
    return {
        "available": available,
        "active": classified["active"],
        "overdue": classified["overdue"],
        # Counts are derived from the exact rows the panes render, so the
        # top strip can never disagree with the lists.
        "counts": {
            "available": len(available),
            "active": len(classified["active"]),
            "overdue": len(classified["overdue"]),
        },
    }


def list_loans(today: str) -> dict:
    c = connect()
    try:
        rows = [
            dict(r)
            for r in c.execute(
                "SELECT loans.*, items.title FROM loans"
                " JOIN items ON items.id=loans.item_id ORDER BY loans.id DESC"
            )
        ]
    finally:
        c.close()
    return rules.classify_loans(rows, today)
