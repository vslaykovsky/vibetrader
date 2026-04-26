from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator


MacdOutputKey = Literal["macd", "signal", "histogram"]
BbOutputKey = Literal["bb_lower", "bb_middle", "bb_upper"]
StochasticOutputKey = Literal["stoch_k", "stoch_d"]


class LwcMarker(BaseModel):
    model_config = ConfigDict(extra="allow")
    time: str | int | float
    position: str
    color: str
    shape: str
    text: str = ""


class LwcCandlestickPoint(BaseModel):
    model_config = ConfigDict(extra="allow")
    time: str | int | float
    open: float
    high: float
    low: float
    close: float


class LwcTimeValuePoint(BaseModel):
    model_config = ConfigDict(extra="allow")
    time: str | int | float
    value: float


class _LwcSeriesBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    options: dict[str, Any] = Field(default_factory=dict)
    markers: list[LwcMarker] | None = None


class LwcCandlestickSeries(_LwcSeriesBase):
    type: Literal["Candlestick"] = "Candlestick"
    data: list[LwcCandlestickPoint] = Field(default_factory=list)


LwcTimeValueSeriesKind = Literal["Line", "Area", "Histogram", "Baseline", "Bar"]


class LwcTimeValueSeries(_LwcSeriesBase):
    type: LwcTimeValueSeriesKind
    data: list[LwcTimeValuePoint] = Field(default_factory=list)


LwcSeries = Annotated[
    LwcCandlestickSeries | LwcTimeValueSeries,
    Field(discriminator="type"),
]


class LightweightChartsChart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["lightweight-charts"] = "lightweight-charts"
    title: str
    series: list[LwcSeries] = Field(default_factory=list)


class PlotlyChart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["plotly"] = "plotly"
    title: str
    data: list[dict[str, Any]] = Field(default_factory=list)
    layout: dict[str, Any] = Field(default_factory=dict)


class TableChart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["table"] = "table"
    title: str = ""
    rows: list[dict[str, Any]] = Field(default_factory=list)


Chart = Annotated[
    LightweightChartsChart | PlotlyChart | TableChart,
    Field(discriminator="type"),
]


class Ohlc(BaseModel):
    model_config = ConfigDict(extra="forbid")
    open: float
    high: float
    low: float
    close: float
    volume: float


class InputOhlcDataPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ohlc"] = "ohlc"
    id: str = ""
    ticker: str
    ohlc: Ohlc
    closed: bool = True


class InputIndicatorDataPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["indicator"] = "indicator"
    id: str = ""
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


class InputRenkoDataPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["renko"] = "renko"
    id: str = ""
    ticker: str
    brick_size: float = Field(gt=0)
    open: float
    close: float
    direction: Literal["up", "down"]
    closed: bool = True


InputDataPoint = Annotated[
    InputOhlcDataPoint
    | InputIndicatorDataPoint
    | InputPortfolioDataPoint
    | InputRenkoDataPoint,
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
    id: str | None = None
    ticker: str
    scale: str
    update_scale: str | None = None
    partial: bool = False


class SmaIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["sma"] = "sma"
    id: str | None = None
    ticker: str
    scale: str
    period: int
    update_scale: str | None = None
    partial: bool = False


class EmaIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ema"] = "ema"
    id: str | None = None
    ticker: str
    scale: str
    period: int
    update_scale: str | None = None
    partial: bool = False


class MacdIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["macd"] = "macd"
    id: str | None = None
    ticker: str
    scale: str
    fast_period: int
    slow_period: int
    signal_period: int
    outputs: list[MacdOutputKey] = Field(
        default_factory=lambda: ["macd", "signal", "histogram"]
    )
    update_scale: str | None = None
    partial: bool = False

    @model_validator(mode="after")
    def _macd_outputs(self) -> MacdIndicatorSubscription:
        if not self.outputs:
            raise ValueError("outputs must be non-empty")
        if len(self.outputs) != len(set(self.outputs)):
            raise ValueError("outputs must not contain duplicates")
        return self


class RsiIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["rsi"] = "rsi"
    id: str | None = None
    ticker: str
    scale: str
    period: int
    update_scale: str | None = None
    partial: bool = False


class AtrIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["atr"] = "atr"
    id: str | None = None
    ticker: str
    scale: str
    period: int
    update_scale: str | None = None
    partial: bool = False


class BollingerBandsIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["bb"] = "bb"
    id: str | None = None
    ticker: str
    scale: str
    period: int = Field(default=20, ge=1)
    std_dev: float = Field(default=2.0, gt=0)
    outputs: list[BbOutputKey] = Field(
        default_factory=lambda: ["bb_middle", "bb_upper", "bb_lower"]
    )
    update_scale: str | None = None
    partial: bool = False

    @model_validator(mode="after")
    def _bb_outputs(self) -> BollingerBandsIndicatorSubscription:
        if not self.outputs:
            raise ValueError("outputs must be non-empty")
        if len(self.outputs) != len(set(self.outputs)):
            raise ValueError("outputs must not contain duplicates")
        return self


class StochasticIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["stochastic"] = "stochastic"
    id: str | None = None
    ticker: str
    scale: str
    k_period: int = Field(default=14, ge=1)
    k_slowing: int = Field(default=3, ge=1)
    d_period: int = Field(default=3, ge=1)
    outputs: list[StochasticOutputKey] = Field(
        default_factory=lambda: ["stoch_k", "stoch_d"]
    )
    update_scale: str | None = None
    partial: bool = False

    @model_validator(mode="after")
    def _stoch_outputs(self) -> StochasticIndicatorSubscription:
        if not self.outputs:
            raise ValueError("outputs must be non-empty")
        if len(self.outputs) != len(set(self.outputs)):
            raise ValueError("outputs must not contain duplicates")
        return self


class FibonacciIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["fibonacci"] = "fibonacci"
    id: str | None = None
    ticker: str
    scale: str
    lookback: int = Field(default=50, ge=2)
    levels: list[float] = Field(
        default_factory=lambda: [0.236, 0.382, 0.5, 0.618, 0.786]
    )
    update_scale: str | None = None
    partial: bool = False


class RenkoIndicatorSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["renko"] = "renko"
    id: str | None = None
    ticker: str
    scale: str
    brick_size: float = Field(gt=0)
    update_scale: str | None = None
    partial: bool = False


IndicatorSubscriptionSpec = Annotated[
    SmaIndicatorSubscription
    | EmaIndicatorSubscription
    | MacdIndicatorSubscription
    | RsiIndicatorSubscription
    | AtrIndicatorSubscription
    | BollingerBandsIndicatorSubscription
    | StochasticIndicatorSubscription
    | FibonacciIndicatorSubscription
    | RenkoIndicatorSubscription,
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


class OutputChart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["chart"] = "chart"
    chart: Chart


OutputDataPoint = Annotated[
    OutputIndicatorDataPoint
    | OutputMarketTradeOrder
    | OutputTickerSubscription
    | OutputIndicatorSubscriptionOrder
    | OutputTimeAck
    | OutputChart,
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
