import os, sqlite3
from pathlib import Path

def db_path() -> Path:
    d = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "borrowboard.db"

def connect():
    # isolation_level=None: the commit layer manages transactions explicitly
    # (BEGIN IMMEDIATE / COMMIT), so the driver never opens implicit ones
    # behind our back - the write-back granularity stays pinned in one place.
    c = sqlite3.connect(db_path(), isolation_level=None, timeout=30.0)
    c.row_factory = sqlite3.Row
    # WAL is set once at schema bootstrap (it persists in the file). NORMAL
    # sync keeps commits cheap on this overlay-backed volume while still
    # being crash-consistent within WAL.
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA foreign_keys=ON")
    return c
