import json
import tempfile
from pathlib import Path

from services.agent import _merge_parameters_json_into_params_file


def test_merge_parameters_json_into_params_file_accepts_object_and_merges():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "params.json").write_text(json.dumps({"a": 1, "x": {"p": 1}}), encoding="utf-8")

        _merge_parameters_json_into_params_file(root, {"b": 2, "x": {"q": 2}})

        assert json.loads((root / "params.json").read_text(encoding="utf-8")) == {
            "a": 1,
            "b": 2,
            "x": {"p": 1, "q": 2},
        }

