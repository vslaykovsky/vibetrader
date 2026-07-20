from __future__ import annotations

import os
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from db.session import engine, init_database

init_database(engine)


@pytest.fixture(autouse=True)
def _accepted_eula(monkeypatch):
    monkeypatch.setattr("auth._eula_access_error", lambda _user_id: None)
