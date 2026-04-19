"""Minimal stdin/stdout strategy for ``StrategyRuntime`` tests (not the product SMA strategy)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from strategies_v2.utils import (  # noqa: E402
    OutputTickerSubscription,
    OutputTimeAck,
    StrategyInput,
    StrategyOutput,
)

_startup = StrategyOutput(
    [
        OutputTickerSubscription(ticker="TEST", scale="1d"),
    ]
)
print(_startup.model_dump_json(), flush=True)

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    step = StrategyInput.model_validate_json(raw)
    has_bar = any(p.kind == "ohlc" for p in step.points)
    outs = []
    if has_bar:
        outs.append(OutputTimeAck(unixtime=step.unixtime))
    print(StrategyOutput(outs).model_dump_json(), flush=True)
