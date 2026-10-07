"""Pure lending rules.

This module performs NO database access and NO I/O. Callers hand in plain
values / plain dicts; the persistence layer (app.modules.lending) reads the
database and feeds the verdict, and the HTTP layer only wires the two.
"""

AVAILABLE = "available"
ON_LOAN = "on_loan"
ACTIVE = "active"
RETURNED = "returned"


def can_lend(item_status, active_loans):
    """Eligibility for creating a loan. Pure.

    Returns ``{"ok": bool, "reason": str}``. Reasons:
      * "item_not_available" - items.status != 'available'
      * "already_on_loan"    - an active loan row already exists
    """
    if item_status != AVAILABLE:
        return {"ok": False, "reason": "item_not_available"}
    if active_loans > 0:
        return {"ok": False, "reason": "already_on_loan"}
    return {"ok": True, "reason": ""}


def can_return(loan_status):
    """Only an active loan may be returned. Pure."""
    return loan_status == ACTIVE


def is_overdue(due_date, today, loan_status):
    """Overdue iff still active and due_date strictly before today. Pure.

    Returned loans are never overdue, whatever their due_date says.
    """
    if loan_status != ACTIVE:
        return False
    return bool(due_date) and due_date < today


def classify_loans(loans, today):
    """Partition plain loan dicts into active / overdue / returned. Pure."""
    active, overdue, returned = [], [], []
    for loan in loans:
        st = loan.get("status")
        if st == RETURNED:
            returned.append(loan)
        elif is_overdue(loan.get("due_date"), today, st):
            overdue.append({**loan, "overdue": True})
        elif st == ACTIVE:
            active.append({**loan, "overdue": False})
    return {"active": active, "overdue": overdue, "returned": returned}
