"""Catalog (items) and board settings persistence."""

from app.db import connect


def list_items() -> list[dict]:
    c = connect()
    try:
        rows = [dict(r) for r in c.execute("SELECT * FROM items")]
    finally:
        c.close()
    return rows


def create_item(title: str, owner: str) -> int:
    c = connect()
    try:
        cur = c.execute(
            "INSERT INTO items(title,owner,status,data_quality) VALUES (?,?,?,?)",
            (title, owner, "available", "clean"),
        )
    finally:
        c.close()
    return cur.lastrowid


def get_settings() -> dict:
    c = connect()
    try:
        rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}
    finally:
        c.close()
    return rows
