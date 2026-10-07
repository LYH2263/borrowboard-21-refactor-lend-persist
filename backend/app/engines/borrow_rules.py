"""Pure lending rules: eligibility and overdue/board classification.

Pure by contract — this module must never import ``app.db``, never open a
sqlite connection and do no I/O. Every result is a function of its arguments.
The commit layer (``app.services.lending``) takes a database snapshot, runs
these functions on it, and is the only layer that writes anything back.
"""

AVAILABLE = "available"
ON_LOAN = "on_loan"
ACTIVE = "active"
RETURNED = "returned"


def can_lend(item_status: str, active_loans: int) -> dict:
    """Eligibility for one item: free status and zero active loans."""
    if item_status != AVAILABLE:
        return {"ok": False, "reason": "item_not_available"}
    if active_loans > 0:
        return {"ok": False, "reason": "already_on_loan"}
    return {"ok": True, "reason": ""}


def evaluate_lend(item: dict, active_loans: int, request: dict) -> dict:
    """Run the whole lend calculus on in-memory snapshots.

    ``item`` is an item-row snapshot, ``active_loans`` is the number of
    active loans for it, ``request`` carries ``borrower``/``due_date``.
    On success the result carries the exact loan row the commit layer is
    allowed to write — nothing else may be persisted. On rejection nothing
    in the result is writable, so the caller writes nothing at all.
    """
    decision = can_lend(item.get("status"), active_loans)
    if not decision["ok"]:
        return decision
    return {
        "ok": True,
        "reason": "",
        "item_id": item["id"],
        "loan": {
            "item_id": item["id"],
            "borrower": request["borrower"],
            "status": ACTIVE,
            "due_date": request["due_date"],
        },
    }


def is_overdue(due_date: str, today: str, loan_status: str) -> bool:
    # Returned loans are settled and can never be overdue, due date or not.
    if loan_status != ACTIVE:
        return False
    return bool(due_date) and due_date < today


def classify_loans(loans: list[dict], today: str) -> dict:
    """Split loan snapshots into active / overdue / returned (pure)."""
    active, overdue, returned = [], [], []
    for L in loans:
        st = L.get("status")
        if st == RETURNED:
            returned.append(L)
        elif is_overdue(L.get("due_date"), today, st):
            overdue.append({**L, "overdue": True})
        elif st == ACTIVE:
            active.append({**L, "overdue": False})
    return {"active": active, "overdue": overdue, "returned": returned}


def build_board(items: list[dict], loans: list[dict], today: str) -> dict:
    """Assemble the board from one consistent snapshot (pure).

    The top strip counts and the two panes are derived from the very same
    lists, so ``counts.available`` always equals the number of rows in the
    available pane — they cannot disagree about the world.
    """
    available = [dict(i) for i in items if i.get("status") == AVAILABLE]
    classes = classify_loans(loans, today)
    active, overdue = classes["active"], classes["overdue"]
    return {
        "available": available,
        "active": active,
        "overdue": overdue,
        "counts": {
            "available": len(available),
            "active": len(active),
            "overdue": len(overdue),
        },
    }
