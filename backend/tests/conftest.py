from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
