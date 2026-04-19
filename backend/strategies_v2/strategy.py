import json
import sys
from pathlib import Path

from utils import *


params = json.loads(Path(__file__).with_name("params.json").read_text())
ticker = params["ticker"]
scale = params["scale"]
sma_period = int(params["sma"]["period"])
deposit_fraction = float(params["orders"]["deposit_fraction"])

in_position = False
sma_value = None

startup = StrategyOutput(
    [
        OutputTickerSubscription(ticker=ticker, scale=scale),
        OutputIndicatorSubscriptionOrder(
            indicator=SmaIndicatorSubscription(ticker=ticker, scale=scale, period=sma_period)
        ),
    ]
)
print(startup.model_dump_json(), flush=True)

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    step = StrategyInput.model_validate_json(raw)
    outputs: list[OutputDataPoint] = []
    has_bar_data = False
    close = None

    for point in step.points:
        if point.kind == "portfolio":
            in_position = any(
                position.ticker == ticker and position.order_type.lower() == "long" and position.deposit_ratio > 0
                for position in point.positions
            )
        elif point.kind == "ohlc" and point.ticker == ticker:
            has_bar_data = True
            close = point.ohlc.close
        elif point.kind == "indicator" and point.name == "sma":
            has_bar_data = True
            sma_value = point.value

    if close is not None and sma_value is not None:
        outputs.append(OutputIndicatorDataPoint(kind="indicator", unixtime=step.unixtime, name="sma", value=sma_value))
        if not in_position and close > sma_value:
            outputs.append(
                OutputMarketTradeOrder(
                    ticker=ticker,
                    direction="buy",
                    deposit_ratio=deposit_fraction,
                )
            )
            in_position = True
        elif in_position and close < sma_value:
            outputs.append(
                OutputMarketTradeOrder(
                    ticker=ticker,
                    direction="sell",
                    deposit_ratio=deposit_fraction,
                )
            )
            in_position = False

    if has_bar_data:
        outputs.append(OutputTimeAck(unixtime=step.unixtime))

    print(StrategyOutput(outputs).model_dump_json(), flush=True)
