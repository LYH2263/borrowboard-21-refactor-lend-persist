"""Test isolation: every test gets a fresh on-disk SQLite database."""

import pytest

from app import seed


@pytest.fixture()
def db_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    seed.init_db()
    return tmp_path / "data"
