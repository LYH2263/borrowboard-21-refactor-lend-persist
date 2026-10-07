import pytest

from app import seed


@pytest.fixture
def db_dir(tmp_path, monkeypatch):
    """Each test gets its own freshly seeded sqlite file."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed.init_db()
    return tmp_path
