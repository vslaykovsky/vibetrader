from __future__ import annotations

import sys
import time
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from strategies_v2.utils import OutputTickerSubscription, StrategyOutput  # noqa: E402

print(
    StrategyOutput([OutputTickerSubscription(ticker="TEST", scale="1d")]).model_dump_json(),
    flush=True,
)
print('{"kind":"indicator_series_catalog","series":[]}', flush=True)
time.sleep(10)
