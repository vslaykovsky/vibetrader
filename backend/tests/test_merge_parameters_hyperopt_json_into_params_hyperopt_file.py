import json
import tempfile
from pathlib import Path

from services.agent import _merge_parameters_hyperopt_json_into_params_hyperopt_file


def test_merge_parameters_hyperopt_json_into_params_hyperopt_file_accepts_object_and_merges():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "params-hyperopt.json").write_text(
            json.dumps({"n_trials": 10, "x": {"p": 1}}), encoding="utf-8"
        )

        _merge_parameters_hyperopt_json_into_params_hyperopt_file(
            root, {"n_trials": 20, "x": {"q": 2}}
        )

        assert json.loads((root / "params-hyperopt.json").read_text(encoding="utf-8")) == {
            "n_trials": 20,
            "x": {"p": 1, "q": 2},
        }
