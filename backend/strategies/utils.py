import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from alpaca.data.enums import CryptoFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from moexalgo import session as moex_session
from moexalgo import Ticker


OUTPUT_DIR = Path(__file__).resolve().parent
PARAMS_PATH = OUTPUT_DIR / "params.json"
BACKTEST_PATH = OUTPUT_DIR / "backtest.json"
DATA_PATH = BACKTEST_PATH
METRICS_PATH = OUTPUT_DIR / "metrics.json"
PARAMS_HYPEROPT_PATH = OUTPUT_DIR / "params-hyperopt.json"
AVAILABLE_PROVIDERS = {"auto", "alpaca", "moex"}


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


Chart = Annotated[LightweightChartsChart | PlotlyChart, Field(discriminator="type")]


class Metrics(BaseModel):
    model_config = ConfigDict(extra="ignore")
    total_return: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    win_rate: float | None = None
    num_trades: int | None = None
    final_equity: float | None = None


class DataJson(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_name: str
    charts: list[Chart] = Field(default_factory=list)
    table: list[dict[str, Any]] = Field(default_factory=list)
    metrics: Metrics | None = None


@dataclass
class Trade:
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    return_pct: float
    reason: str


def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_params() -> dict:
    with PARAMS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def serialize_data_json(document: DataJson) -> dict[str, Any]:
    return document.model_dump(mode="json", exclude_none=True)


def save_data_json(document: DataJson, path: Path | None = None) -> None:
    target = path or DATA_PATH
    if path is None:
        ensure_output_dir()
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
    save_json(target, serialize_data_json(document))


def normalize_timeframe(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized in {"1day", "day", "1d"}:
        return "1d"
    if normalized in {"1hour", "hour", "1h"}:
        return "1h"
    if normalized in {"1min", "1minute", "minute", "1m"}:
        return "1m"
    if normalized in {"1week", "week", "weekly", "1w", "w"}:
        return "1w"
    raise ValueError(f"Unsupported timeframe: {value}")


def timeframe_from_string(value: str) -> TimeFrame:
    normalized = normalize_timeframe(value)
    if normalized == "1d":
        return TimeFrame.Day
    if normalized == "1h":
        return TimeFrame.Hour
    if normalized == "1w":
        return TimeFrame.Week
    return TimeFrame.Minute


def period_from_string(value: str) -> int:
    normalized = normalize_timeframe(value)
    if normalized == "1d":
        return 24
    if normalized == "1h":
        return 60
    if normalized == "1w":
        return 7
    return 1


def normalize_crypto_symbol(ticker: str) -> str:
    t = ticker.strip().upper()
    if "/" in t:
        return t
    if len(t) > 3 and t.endswith("USD"):
        return f"{t[:-3]}/USD"
    return t


def normalize_provider(provider: str) -> str:
    if provider in {"moexalgo", "algopack"}:
        provider = "moex"
    if provider not in AVAILABLE_PROVIDERS:
        raise RuntimeError("MARKET_DATA_PROVIDER must be one of: auto, alpaca, moex")
    return provider


def _as_ohlcv_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out = out.rename(columns=str.lower)
    out = out[["open", "high", "low", "close", "volume"]]
    out.index = pd.to_datetime(out.index)
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    out = out.sort_index()
    out.index.name = None
    return out


def _drop_wide_spread_bars(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    hi = out["high"].astype(float)
    lo = out["low"].astype(float)
    op = out["open"].astype(float)
    cl = out["close"].astype(float)
    spread = hi - lo
    body = (cl - op).abs()
    keep = (hi > 0) & (lo > 0) & (op > 0)
    keep &= spread <= 0.3 * hi
    keep &= spread <= 0.3 * lo
    keep &= body <= 0.3 * op
    return out.loc[keep]


def _end_datetime_capped_yesterday(end_test_date: str) -> datetime:
    # Subscription doesn't allow recent data.
    cap_date = (datetime.now(timezone.utc).date() - timedelta(days=1))
    end = datetime.fromisoformat(end_test_date)
    if end.date() > cap_date:
        return datetime.combine(cap_date, end.time(), tzinfo=end.tzinfo)
    return end


def _market_data_provider_name(provider: Optional[str]) -> str:
    if provider is not None:
        return normalize_provider(provider)
    
    raw = os.environ.get("MARKET_DATA_PROVIDER")
    if not raw or not raw.strip():
        return "auto"

    provider = raw.strip().lower()
    return normalize_provider(provider)


def _alpaca_keys() -> tuple[str, str]:
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set")
    return api_key, secret_key


def _moex_keys() -> str:
    api_key = (os.environ.get("MOEX_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("MOEX_API_KEY must be set")
    return api_key


def _fetch_moex_bars(
    ticker: str,
    start_test_date: str,
    end_test_date: str,
    history_padding_days: int,
    timeframe: str,
) -> pd.DataFrame:
    moex_session.TOKEN = _moex_keys()
    period = period_from_string(timeframe)

    start = datetime.fromisoformat(start_test_date) - timedelta(days=int(history_padding_days))
    end = _end_datetime_capped_yesterday(end_test_date)
    secid = ticker.strip().upper()

    bars = Ticker(secid).candles(
        start=start.date().isoformat(),
        end=end.date().isoformat(),
        period=period,
    )
    df = bars if isinstance(bars, pd.DataFrame) else pd.DataFrame(bars)
    if df.empty:
        raise RuntimeError("No market data returned from MOEX.")

    required_cols = ("begin", "open", "high", "low", "close", "volume")
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise RuntimeError(f"MOEX candles response missing columns: {', '.join(missing)}")

    shaped = df.copy()
    shaped["begin"] = pd.to_datetime(shaped["begin"], errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        shaped[col] = pd.to_numeric(shaped[col], errors="coerce")
    shaped = shaped.dropna(subset=["begin", "open", "high", "low", "close", "volume"])
    shaped = shaped.set_index("begin")
    return _drop_wide_spread_bars(_as_ohlcv_dataframe(shaped))


def _fetch_alpaca_bars(
    ticker: str,
    start_test_date: str,
    end_test_date: str,
    history_padding_days: int,
    timeframe: str,
) -> pd.DataFrame:
    api_key, secret_key = _alpaca_keys()
    tf = timeframe_from_string(timeframe)
    client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
    start = datetime.fromisoformat(start_test_date) - timedelta(days=int(history_padding_days))
    end = _end_datetime_capped_yesterday(end_test_date)
    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        start=start,
        end=end,
        timeframe=tf,
    )
    bars = client.get_stock_bars(request)
    df = bars.df if hasattr(bars, "df") else pd.DataFrame(bars)
    if df.empty:
        raise RuntimeError("No market data returned from Alpaca.")
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level=0).copy()
    else:
        df = df.copy()
    return _drop_wide_spread_bars(_as_ohlcv_dataframe(df))


def fetch_stock_bars(
    ticker: str,
    start_test_date: str,
    end_test_date: str,
    history_padding_days: int,
    timeframe: str,
    provider: Optional[str] = None,
) -> pd.DataFrame:
    provider = _market_data_provider_name(provider=provider)
    if provider == "alpaca":
        return _fetch_alpaca_bars(
            ticker=ticker,
            start_test_date=start_test_date,
            end_test_date=end_test_date,
            history_padding_days=history_padding_days,
            timeframe=timeframe,
        )
    if provider == "moex":
        return _fetch_moex_bars(
            ticker=ticker,
            start_test_date=start_test_date,
            end_test_date=end_test_date,
            history_padding_days=history_padding_days,
            timeframe=timeframe,
        )

    # auto mode: try Alpaca first for global symbols; fallback to MOEX.
    alpaca_exc: Exception | None = None
    try:
        return _fetch_alpaca_bars(
            ticker=ticker,
            start_test_date=start_test_date,
            end_test_date=end_test_date,
            history_padding_days=history_padding_days,
            timeframe=timeframe,
        )
    except Exception as exc:
        alpaca_exc = exc

    try:
        return _fetch_moex_bars(
            ticker=ticker,
            start_test_date=start_test_date,
            end_test_date=end_test_date,
            history_padding_days=history_padding_days,
            timeframe=timeframe,
        )
    except Exception as moex_exc:
        raise RuntimeError(
            "Unable to fetch market data with auto provider selection. "
            f"Alpaca error: {alpaca_exc}; MOEX error: {moex_exc}"
        ) from moex_exc


def fetch_crypto_bars(
    ticker: str,
    start_test_date: str,
    end_test_date: str,
    timeframe: str,
) -> pd.DataFrame:
    api_key, secret_key = _alpaca_keys()
    tf = timeframe_from_string(timeframe)
    client = CryptoHistoricalDataClient(api_key, secret_key)
    symbol = normalize_crypto_symbol(ticker)
    start = pd.Timestamp(start_test_date, tz="UTC")
    end = pd.Timestamp(_end_datetime_capped_yesterday(end_test_date), tz="UTC") + pd.Timedelta(days=1)
    request = CryptoBarsRequest(
        symbol_or_symbols=symbol,
        start=start.to_pydatetime(),
        end=end.to_pydatetime(),
        timeframe=tf,
    )
    barset = client.get_crypto_bars(request, feed=CryptoFeed.US)
    df = barset.df.copy()
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
        if "timestamp" in df.columns:
            df = df.rename(columns={"timestamp": "time"})
        elif "index" in df.columns:
            df = df.rename(columns={"index": "time"})
    elif "timestamp" in df.columns:
        df = df.rename(columns={"timestamp": "time"})
    if "symbol" in df.columns:
        df = df[df["symbol"] == symbol].copy()
    if "time" not in df.columns:
        df = df.reset_index().rename(columns={df.index.name or "index": "time"})
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time")
    if "symbol" in df.columns:
        df = df.drop(columns=["symbol"])
    df = df.set_index("time")
    return _drop_wide_spread_bars(_as_ohlcv_dataframe(df))
