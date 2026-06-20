from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_STRATEGIES_V2 = _ROOT / "strategies_v2"
if str(_STRATEGIES_V2) not in sys.path:
    sys.path.insert(0, str(_STRATEGIES_V2))

from strategies_v2.hyperopt import (
    STRATEGY_DEADLOCK_EXIT_CODE,
    _active_search_space,
    _strategy_deadlock_message,
)
from utils import ParamsHyperopt


def test_strategy_deadlock_message_detects_runtime_deadlock():
    stderr = "strategy deadlock: No stdout line within 5.0s after send. stderr=''"

    assert (
        _strategy_deadlock_message(
            returncode=STRATEGY_DEADLOCK_EXIT_CODE,
            stdout="",
            stderr=stderr,
        )
        == "strategy deadlock detected: strategy deadlock: No stdout line within 5.0s after send. stderr=''"
    )


def test_active_search_space_applies_include_then_exclude():
    cfg = ParamsHyperopt.model_validate(
        {
            "search_space": {
                "fast_period": {"type": "int", "low": 4, "high": 12},
                "slow_period": {"type": "int", "low": 20, "high": 80},
                "deposit_fraction": {"type": "float", "low": 0.1, "high": 1.0},
            },
            "included_parameters": ["fast_period", "slow_period"],
            "excluded_parameters": ["slow_period"],
        }
    )

    assert _active_search_space(cfg) == {
        "fast_period": cfg.search_space["fast_period"],
    }
