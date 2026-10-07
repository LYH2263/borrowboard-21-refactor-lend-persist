import os, sqlite3
from contextlib import contextmanager
from pathlib import Path

def db_path() -> Path:
    d = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "borrowboard.db"

def connect():
    c = sqlite3.connect(db_path())
    c.row_factory = sqlite3.Row
    # A second concurrent BEGIN IMMEDIATE waits for the first commit instead
    # of failing instantly with SQLITE_BUSY, then reads the committed world.
    c.execute("PRAGMA busy_timeout=5000")
    return c

@contextmanager
def read_transaction():
    """Read-only snapshot: every SELECT in the block sees the same world."""
    c = connect()
    c.isolation_level = None
    c.execute("BEGIN")  # deferred: all reads share one snapshot
    try:
        yield c
    finally:
        c.rollback()
        c.close()

@contextmanager
def write_transaction():
    """One serialised read-calc-write unit.

    ``BEGIN IMMEDIATE`` takes the write lock up front, so two concurrent
    lend submissions are serialised: the second blocks here until the first
    commits, and then reads the already-on_loan world. Every statement in
    the block — snapshot reads, pure-rule calculus and the paired writes —
    shares one transaction, so the item status flip and the loan insert are
    committed together or not at all.
    """
    c = connect()
    c.isolation_level = None  # autocommit mode: we manage the transaction
    try:
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
