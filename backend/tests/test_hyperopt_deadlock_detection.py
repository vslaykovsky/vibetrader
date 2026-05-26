from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_STRATEGIES_V2 = _ROOT / "strategies_v2"
if str(_STRATEGIES_V2) not in sys.path:
    sys.path.insert(0, str(_STRATEGIES_V2))

from strategies_v2.hyperopt import STRATEGY_DEADLOCK_EXIT_CODE, _strategy_deadlock_message


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
