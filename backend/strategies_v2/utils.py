from typing import Annotated, Any, Literal

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
    closed: bool = True


class InputIndicatorDataPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["indicator"] = "indicator"
    name: str
    value: float
    closed: bool = True


class PortfolioPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker: str
    order_type: Literal["long", "short"]
    deposit_ratio: float = Field(ge=0, le=1)
    volume_weighted_avg_entry_price: float = Field(gt=0)


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
    update_scale: str | None = None
    partial: bool = False


class SmaIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["sma"] = "sma"
    ticker: str
    scale: str
    period: int
    update_scale: str | None = None
    partial: bool = False


class EmaIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ema"] = "ema"
    ticker: str
    scale: str
    period: int
    update_scale: str | None = None
    partial: bool = False


class MacdIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["macd"] = "macd"
    ticker: str
    scale: str
    fast_period: int
    slow_period: int
    signal_period: int
    update_scale: str | None = None
    partial: bool = False


class RsiIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["rsi"] = "rsi"
    ticker: str
    scale: str
    period: int
    update_scale: str | None = None
    partial: bool = False


class AtrIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["atr"] = "atr"
    ticker: str
    scale: str
    period: int
    update_scale: str | None = None
    partial: bool = False


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


class HyperoptIntSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["int"] = "int"
    low: int
    high: int


class HyperoptFloatSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["float"] = "float"
    low: float
    high: float


class HyperoptCategoricalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["categorical"] = "categorical"
    choices: list[Any]


HyperoptSearchSpec = Annotated[
    HyperoptIntSpec | HyperoptFloatSpec | HyperoptCategoricalSpec,
    Field(discriminator="type"),
]


class ParamsHyperopt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search_space: dict[str, HyperoptSearchSpec]
    n_trials: int = 30
    timeout_seconds: int = 120
    direction: Literal["maximize", "minimize"] = "maximize"
    objective_metric: str = "total_return"
    seed: int | None = None
    trial_timeout_seconds: int | None = None
