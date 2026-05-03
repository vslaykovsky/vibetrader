from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from strategies_v2.utils import (  # noqa: E402
    OutputIndicatorSeriesCatalog,
    OutputIndicatorSeriesCatalogEntry,
    OutputTickerSubscription,
    OutputTimeAck,
    StrategyInput,
    StrategyOutput,
)

print(
    StrategyOutput([OutputTickerSubscription(ticker="TEST", scale="1d")]).model_dump_json(),
    flush=True,
)
print(
    StrategyOutput(
        [
            OutputIndicatorSeriesCatalog(
                series=[
                    OutputIndicatorSeriesCatalogEntry(
                        name="split_startup",
                        description="Split startup fixture",
                    )
                ]
            )
        ]
    ).model_dump_json(),
    flush=True,
)

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    step = StrategyInput.model_validate_json(raw)
    print(StrategyOutput([OutputTimeAck(unixtime=step.unixtime)]).model_dump_json(), flush=True)
