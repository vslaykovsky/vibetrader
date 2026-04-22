"""Fixture strategy for ``StrategyRuntime.finalize`` tests: emits an ``OutputChart`` after stdin EOF."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from strategies_v2.utils import (  # noqa: E402
    LwcTimeValuePoint,
    LwcTimeValueSeries,
    LightweightChartsChart,
    OutputChart,
    OutputTickerSubscription,
    OutputTimeAck,
    StrategyInput,
    StrategyOutput,
)

_startup = StrategyOutput([OutputTickerSubscription(ticker="TEST", scale="1d")])
print(_startup.model_dump_json(), flush=True)

closes: list[tuple[int, float]] = []
for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    step = StrategyInput.model_validate_json(raw)
    outs = []
    has_bar = False
    for p in step.points:
        if p.kind == "ohlc":
            has_bar = True
            closes.append((step.unixtime, p.ohlc.close))
    if has_bar:
        outs.append(OutputTimeAck(unixtime=step.unixtime))
    print(StrategyOutput(outs).model_dump_json(), flush=True)

final_chart = LightweightChartsChart(
    title="Close series",
    series=[
        LwcTimeValueSeries(
            type="Line",
            label="close",
            data=[LwcTimeValuePoint(time=int(t), value=float(v)) for t, v in closes],
        )
    ],
)
print(StrategyOutput([OutputChart(chart=final_chart)]).model_dump_json(), flush=True)
