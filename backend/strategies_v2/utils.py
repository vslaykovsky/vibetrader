from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel


class Ohlc(BaseModel):
    model_config = ConfigDict(extra="forbid")
    open: float
    high: float
    low: float
    close: float


class InputOhlcDataPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ohlc"] = "ohlc"
    ticker: str
    ohlc: Ohlc


class InputIndicatorDataPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["indicator"] = "indicator"
    name: str
    value: float


class PortfolioPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker: str
    order_type: str
    deposit_ratio: float = Field(ge=0, le=1)


class InputPortfolioDataPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["portfolio"] = "portfolio"
    positions: list[PortfolioPosition]


InputDataPoint = Annotated[
    InputOhlcDataPoint | InputIndicatorDataPoint | InputPortfolioDataPoint,
    Field(discriminator="kind"),
]


class StrategyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unixtime: int
    points: list[InputDataPoint]


class OutputIndicatorDataPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["indicator"] = "indicator"
    unixtime: int
    name: str
    value: float


class OutputMarketTradeOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["market_order"] = "market_order"
    ticker: str
    direction: str
    deposit_ratio: float = Field(default=1.0, ge=0, le=1)


class OutputTickerSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ticker_subscription"] = "ticker_subscription"
    ticker: str
    scale: str


class SmaIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["sma"] = "sma"
    ticker: str
    scale: str
    period: int


class EmaIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ema"] = "ema"
    ticker: str
    scale: str
    period: int


class MacdIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["macd"] = "macd"
    ticker: str
    scale: str
    fast_period: int
    slow_period: int
    signal_period: int


class RsiIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["rsi"] = "rsi"
    ticker: str
    scale: str
    period: int


class AtrIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["atr"] = "atr"
    ticker: str
    scale: str
    period: int


IndicatorSubscriptionSpec = Annotated[
    SmaIndicatorSubscription
    | EmaIndicatorSubscription
    | MacdIndicatorSubscription
    | RsiIndicatorSubscription
    | AtrIndicatorSubscription,
    Field(discriminator="kind"),
]


class OutputIndicatorSubscriptionOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["indicator_subscription"] = "indicator_subscription"
    indicator: IndicatorSubscriptionSpec


class OutputTimeAck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["time_ack"] = "time_ack"
    unixtime: int


OutputDataPoint = Annotated[
    OutputIndicatorDataPoint
    | OutputMarketTradeOrder
    | OutputTickerSubscription
    | OutputIndicatorSubscriptionOrder
    | OutputTimeAck,
    Field(discriminator="kind"),
]


class StrategyOutput(RootModel[list[OutputDataPoint]]):
    pass
