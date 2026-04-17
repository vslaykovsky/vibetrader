import json
import tempfile
from pathlib import Path

from services.agent import read_strategy_name_from_workspace


def test_read_strategy_name_from_workspace():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "params.json").write_text(
            json.dumps({"strategy_name": "  Momentum pulse  "}),
            encoding="utf-8",
        )
        assert read_strategy_name_from_workspace(root) == "Momentum pulse"
