import json
import tempfile
from pathlib import Path

from services.agent import _merge_parameters_hyperopt_json_into_params_hyperopt_file


def test_merge_parameters_hyperopt_json_into_params_hyperopt_file_accepts_object_and_merges():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "params-hyperopt.json").write_text(
            json.dumps(
                {
                    "search_space": {
                        "fast_period": {"type": "int", "low": 4, "high": 12},
                        "slow_period": {"type": "int", "low": 20, "high": 80},
                        "deposit_fraction": {"type": "float", "low": 0.1, "high": 1.0},
                    },
                    "included_parameters": ["fast_period", "slow_period"],
                    "excluded_parameters": ["slow_period"],
                    "n_trials": 10,
                    "direction": "maximize",
                    "objective_metric": "total_return",
                }
            ),
            encoding="utf-8",
        )

        _merge_parameters_hyperopt_json_into_params_hyperopt_file(
            root,
            {
                "n_trials": 20,
                "objective_metric": "sharpe_ratio",
                "included_parameters": ["deposit_fraction"],
                "excluded_parameters": ["fast_period"],
            },
        )

        assert json.loads((root / "params-hyperopt.json").read_text(encoding="utf-8")) == {
            "direction": "maximize",
            "excluded_parameters": ["fast_period"],
            "included_parameters": ["deposit_fraction"],
            "n_trials": 20,
            "objective_metric": "sharpe_ratio",
            "search_space": {
                "deposit_fraction": {"type": "float", "low": 0.1, "high": 1.0},
                "fast_period": {"type": "int", "low": 4, "high": 12},
                "slow_period": {"type": "int", "low": 20, "high": 80},
            },
        }
