import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Annotated, Any, Callable, Literal, Optional

logger = logging.getLogger(__name__)

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from alpaca.data.enums import CryptoFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from moexalgo import session as moex_session
from moexalgo import Ticker


WORKSPACE_DIR = Path(__file__).resolve().parent
PARAMS_PATH = WORKSPACE_DIR / "params.json"
BACKTEST_PATH = WORKSPACE_DIR / "backtest.json"
METRICS_PATH = WORKSPACE_DIR / "metrics.json"
PARAMS_HYPEROPT_PATH = WORKSPACE_DIR / "params-hyperopt.json"
AVAILABLE_PROVIDERS = {"auto", "alpaca", "moex"}

_ALPACA_CRYPTO_BAR_CHUNK_BUDGET = 100_000


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
    metrics: Metrics | None = None


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


def load_params() -> dict:
    with PARAMS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def serialize_data_json(document: DataJson) -> dict[str, Any]:
    return document.model_dump(mode="json", exclude_none=True)


def serialize_params_hyperopt(document: ParamsHyperopt) -> dict[str, Any]:
    return document.model_dump(mode="json", exclude_none=True)


def save_params_hyperopt(document: ParamsHyperopt, path: Path | None = None) -> None:
    target = path or PARAMS_HYPEROPT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    save_json(target, serialize_params_hyperopt(document))


def save_backtest_json(document: DataJson, path: Path | None = None) -> None:
    target = path or BACKTEST_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    save_json(target, serialize_data_json(document))

 

def period_from_timeframe(tf: TimeFrame) -> int:
    """MOEX candle period in minutes (except daily=24, weekly=7 per moexalgo convention)."""
    if tf.unit == TimeFrameUnit.Day and tf.amount == 1:
        return 24
    if tf.unit == TimeFrameUnit.Hour and tf.amount == 1:
        return 60
    if tf.unit == TimeFrameUnit.Hour and tf.amount == 4:
        return 240
    if tf.unit == TimeFrameUnit.Week and tf.amount == 1:
        return 7
    if tf.unit == TimeFrameUnit.Minute and tf.amount == 1:
        return 1
    if tf.unit == TimeFrameUnit.Minute and tf.amount == 15:
        return 15
    raise ValueError(f"Unsupported timeframe for MOEX: {tf}")


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
    dropped = int((~keep).sum())
    if dropped > 0:
        logger.info(
            "_drop_wide_spread_bars filtered %s of %s rows (kept=%s); first_dropped_idx=%s",
            dropped,
            len(out),
            int(keep.sum()),
            out.index[~keep][0] if dropped else None,
        )
    return out.loc[keep]


def _end_datetime_capped_yesterday(end_test_date: str) -> datetime:
    # Subscription doesn't allow recent data.
    cap_date = (datetime.now(timezone.utc).date() - timedelta(days=1))
    end = datetime.fromisoformat(end_test_date)
    if end.date() > cap_date:
        return datetime.combine(cap_date, end.time(), tzinfo=end.tzinfo)
    return end


def _end_datetime_inclusive_eod(end_test_date: str) -> datetime:
    """Treat ``end_test_date`` (ISO date) as the *inclusive end of that day*.

    Most provider clients (Alpaca, MOEX) accept ``end`` as an exclusive
    timestamp boundary; if we forward ``YYYY-MM-DD`` directly it parses to
    ``00:00:00`` and the entire trading session of that calendar day is
    excluded from the response. By bumping ``end`` to the *next* midnight
    UTC (still capped at *yesterday*'s next-midnight to respect the
    subscription) we correctly include intraday bars from the requested
    end date — fixes the pan-history hole at the boundary between
    ``oldest-1`` and ``oldest`` for 4h / 1h / 1m timeframes."""
    capped = _end_datetime_capped_yesterday(end_test_date)
    return capped + timedelta(days=1)


def _clamp_request_window(
    start_test_date: str, end_test_date: str
) -> tuple[str, str] | None:
    """Cap ``end`` at *yesterday* (provider subscription limit). Return ``None`` when
    the resulting window has ``start > end`` and no provider call should be made."""
    cap_end = _end_datetime_capped_yesterday(end_test_date)
    start_dt = datetime.fromisoformat(start_test_date)
    if start_dt.date() > cap_end.date():
        return None
    return start_test_date, cap_end.date().isoformat()


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
    timeframe: TimeFrame,
    *,
    drop_wide_spread_bars: bool = True,
) -> pd.DataFrame:
    moex_session.TOKEN = _moex_keys()
    period = period_from_timeframe(timeframe)

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
    out = _as_ohlcv_dataframe(shaped)
    return _drop_wide_spread_bars(out) if drop_wide_spread_bars else out


def _fetch_alpaca_bars(
    ticker: str,
    start_test_date: str,
    end_test_date: str,
    history_padding_days: int,
    timeframe: TimeFrame,
    *,
    drop_wide_spread_bars: bool = True,
) -> pd.DataFrame:
    api_key, secret_key = _alpaca_keys()
    client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
    start = datetime.fromisoformat(start_test_date) - timedelta(days=int(history_padding_days))
    # ``end`` is *inclusive* end-of-day (next-midnight UTC, capped at
    # subscription cap). Without this, intraday bars on the boundary day
    # are silently dropped because ``2026-03-24`` parses to ``00:00:00``.
    end = _end_datetime_inclusive_eod(end_test_date)
    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        start=start,
        end=end,
        timeframe=timeframe,
    )
    bars = client.get_stock_bars(request)
    df = bars.df if hasattr(bars, "df") else pd.DataFrame(bars)
    if df.empty:
        raise RuntimeError("No market data returned from Alpaca.")
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level=0).copy()
    else:
        df = df.copy()
    out = _as_ohlcv_dataframe(df)
    return _drop_wide_spread_bars(out) if drop_wide_spread_bars else out


def _is_http_429_exception(exc: BaseException) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True

    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) == 429:
        return True

    inner = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if inner is not None and inner is not exc:
        return _is_http_429_exception(inner)

    msg = str(exc)
    lowered = msg.lower()
    if "too many requests" in lowered or "rate limit" in lowered:
        return True
    try:
        payload = json.loads(msg)
        if isinstance(payload, dict):
            message = str(payload.get("message", "")).lower()
            if "too many requests" in message or "rate limit" in message:
                return True
    except Exception:
        pass
    return "429" in msg and ("Too Many Requests" in msg or "too many requests" in msg or "rate limit" in msg)


def _timeframe_duration_seconds(tf: TimeFrame) -> float:
    a = float(tf.amount)
    u = tf.unit
    if u == TimeFrameUnit.Minute:
        return a * 60.0
    if u == TimeFrameUnit.Hour:
        return a * 3600.0
    if u == TimeFrameUnit.Day:
        return a * 86400.0
    if u == TimeFrameUnit.Week:
        return a * 7.0 * 86400.0
    if u == TimeFrameUnit.Month:
        return a * 31.0 * 86400.0
    return a * 3600.0


def _estimate_crypto_bars_between(
    start: pd.Timestamp, end_exclusive: pd.Timestamp, tf: TimeFrame
) -> int:
    if end_exclusive <= start:
        return 0
    sec = float((end_exclusive - start) / pd.Timedelta(seconds=1))
    bar_sec = _timeframe_duration_seconds(tf)
    if bar_sec <= 0:
        bar_sec = 60.0
    return max(1, int(math.ceil(sec / bar_sec)))


def _crypto_largest_chunk_end_exclusive(
    cur: pd.Timestamp,
    end_exclusive: pd.Timestamp,
    tf: TimeFrame,
    budget: int,
) -> pd.Timestamp:
    if cur >= end_exclusive:
        return end_exclusive
    if _estimate_crypto_bars_between(cur, end_exclusive, tf) <= budget:
        return end_exclusive
    bar_sec = max(1.0, _timeframe_duration_seconds(tf))
    low = cur
    high = end_exclusive
    for _ in range(96):
        if (high - low).value <= 1_000_000_000:
            break
        mid = low + (high - low) / 2
        if mid <= low:
            mid = low + pd.Timedelta(seconds=1)
        if _estimate_crypto_bars_between(cur, mid, tf) <= budget:
            low = mid
        else:
            high = mid
    if low <= cur:
        low = cur + pd.Timedelta(seconds=bar_sec)
        if low >= end_exclusive:
            low = end_exclusive
    return low


def _alpaca_crypto_barset_to_ohlcv(barset: Any, symbol: str) -> pd.DataFrame:
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
    return _as_ohlcv_dataframe(df)


@retry(
    retry=retry_if_exception(_is_http_429_exception),
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(8),
    reraise=True,
)
def _fetch_alpaca_crypto_bars_one_window(
    client: CryptoHistoricalDataClient,
    symbol: str,
    start: pd.Timestamp,
    end_exclusive: pd.Timestamp,
    timeframe: TimeFrame,
) -> pd.DataFrame:
    request = CryptoBarsRequest(
        symbol_or_symbols=symbol,
        start=start.to_pydatetime(),
        end=end_exclusive.to_pydatetime(),
        timeframe=timeframe,
    )
    barset = client.get_crypto_bars(request, feed=CryptoFeed.US)
    return _alpaca_crypto_barset_to_ohlcv(barset, symbol)


@retry(
    retry=retry_if_exception(_is_http_429_exception),
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(8),
    reraise=True,
)
def _fetch_alpaca_bars_with_429_backoff(
    ticker: str,
    start_test_date: str,
    end_test_date: str,
    history_padding_days: int,
    timeframe: TimeFrame,
    *,
    drop_wide_spread_bars: bool = True,
) -> pd.DataFrame:
    return _fetch_alpaca_bars(
        ticker=ticker,
        start_test_date=start_test_date,
        end_test_date=end_test_date,
        history_padding_days=history_padding_days,
        timeframe=timeframe,
        drop_wide_spread_bars=drop_wide_spread_bars,
    )


def _fetch_alpaca_crypto_bars(
    ticker: str,
    start_test_date: str,
    end_test_date: str,
    timeframe: TimeFrame,
    *,
    drop_wide_spread_bars: bool = True,
) -> pd.DataFrame:
    api_key, secret_key = _alpaca_keys()
    client = CryptoHistoricalDataClient(api_key, secret_key)
    symbol = normalize_crypto_symbol(ticker)
    cur = pd.Timestamp(start_test_date, tz="UTC")
    end = pd.Timestamp(_end_datetime_capped_yesterday(end_test_date), tz="UTC") + pd.Timedelta(days=1)
    frames: list[pd.DataFrame] = []
    while cur < end:
        if _estimate_crypto_bars_between(cur, end, timeframe) <= _ALPACA_CRYPTO_BAR_CHUNK_BUDGET:
            chunk_end = end
        else:
            chunk_end = _crypto_largest_chunk_end_exclusive(
                cur, end, timeframe, _ALPACA_CRYPTO_BAR_CHUNK_BUDGET
            )
        part = _fetch_alpaca_crypto_bars_one_window(
            client, symbol, cur, chunk_end, timeframe
        )
        if not part.empty:
            frames.append(part)
        cur = chunk_end
    if not frames:
        raise RuntimeError("No market data returned from Alpaca.")
    merged = pd.concat(frames).sort_index()
    merged = merged[~merged.index.duplicated(keep="first")]
    return (
        _drop_wide_spread_bars(merged) if drop_wide_spread_bars else merged
    )


def _fetch_alpaca_crypto_bars_with_429_backoff(
    ticker: str,
    start_test_date: str,
    end_test_date: str,
    timeframe: TimeFrame,
    *,
    drop_wide_spread_bars: bool = True,
) -> pd.DataFrame:
    return _fetch_alpaca_crypto_bars(
        ticker=ticker,
        start_test_date=start_test_date,
        end_test_date=end_test_date,
        timeframe=timeframe,
        drop_wide_spread_bars=drop_wide_spread_bars,
    )


_ProviderFetcher = Callable[..., pd.DataFrame]


def _fetch_with_shrink_retry(
    fetcher: _ProviderFetcher,
    *,
    ticker: str,
    start_test_date: str,
    end_test_date: str,
    history_padding_days: int,
    timeframe: TimeFrame,
    drop_wide_spread_bars: bool = True,
) -> pd.DataFrame:
    """Call ``fetcher``; on failure, halve the request window from the *left* (move
    ``start`` toward ``end``) and retry until the window collapses. Returns an empty
    DataFrame instead of raising once the window is empty — symmetric with how
    providers behave when there is simply no data available."""
    clamped = _clamp_request_window(start_test_date, end_test_date)
    if clamped is None:
        return pd.DataFrame()
    cur_start_s, cur_end_s = clamped
    cur_start = datetime.fromisoformat(cur_start_s)
    cur_end = datetime.fromisoformat(cur_end_s)
    last_exc: Exception | None = None
    while cur_start.date() <= cur_end.date():
        try:
            return fetcher(
                ticker=ticker,
                start_test_date=cur_start.date().isoformat(),
                end_test_date=cur_end.date().isoformat(),
                history_padding_days=history_padding_days,
                timeframe=timeframe,
                drop_wide_spread_bars=drop_wide_spread_bars,
            )
        except Exception as exc:
            last_exc = exc
            span_days = (cur_end.date() - cur_start.date()).days
            if span_days <= 0:
                break
            history_padding_days = 0
            cur_start = cur_start + timedelta(days=max(1, span_days // 2))
    if last_exc is not None:
        logger.warning(
            "fetch_with_shrink_retry exhausted ticker=%s start=%s end=%s last_error=%s",
            ticker,
            start_test_date,
            end_test_date,
            last_exc,
        )
    return pd.DataFrame()


def _fetch_alpaca_with_retry(
    ticker: str,
    start_test_date: str,
    end_test_date: str,
    history_padding_days: int,
    timeframe: TimeFrame,
    *,
    drop_wide_spread_bars: bool = True,
) -> pd.DataFrame:
    return _fetch_with_shrink_retry(
        _fetch_alpaca_bars_with_429_backoff,
        ticker=ticker,
        start_test_date=start_test_date,
        end_test_date=end_test_date,
        history_padding_days=history_padding_days,
        timeframe=timeframe,
        drop_wide_spread_bars=drop_wide_spread_bars,
    )


def _fetch_moex_with_retry(
    ticker: str,
    start_test_date: str,
    end_test_date: str,
    history_padding_days: int,
    timeframe: TimeFrame,
    *,
    drop_wide_spread_bars: bool = True,
) -> pd.DataFrame:
    return _fetch_with_shrink_retry(
        _fetch_moex_bars,
        ticker=ticker,
        start_test_date=start_test_date,
        end_test_date=end_test_date,
        history_padding_days=history_padding_days,
        timeframe=timeframe,
        drop_wide_spread_bars=drop_wide_spread_bars,
    )


def fetch_stock_bars(
    ticker: str,
    start_test_date: str,
    end_test_date: str,
    history_padding_days: int,
    timeframe: TimeFrame,
    provider: Optional[str] = None,
    *,
    drop_wide_spread_bars: bool = True,
) -> pd.DataFrame:
    provider = _market_data_provider_name(provider=provider)
    if provider == "alpaca":
        return _fetch_alpaca_with_retry(
            ticker=ticker,
            start_test_date=start_test_date,
            end_test_date=end_test_date,
            history_padding_days=history_padding_days,
            timeframe=timeframe,
            drop_wide_spread_bars=drop_wide_spread_bars,
        )
    if provider == "moex":
        return _fetch_moex_with_retry(
            ticker=ticker,
            start_test_date=start_test_date,
            end_test_date=end_test_date,
            history_padding_days=history_padding_days,
            timeframe=timeframe,
            drop_wide_spread_bars=drop_wide_spread_bars,
        )

    # auto mode: try Alpaca first for global symbols; fallback to MOEX.
    # Keep provider selection fast (single attempt per provider, with 429 backoff on
    # Alpaca). Window shrinking is reserved for explicit provider selection.
    clamped = _clamp_request_window(start_test_date, end_test_date)
    if clamped is None:
        return pd.DataFrame()
    cur_start_s, cur_end_s = clamped

    alpaca_exc: Exception | None = None
    try:
        df = _fetch_alpaca_bars_with_429_backoff(
            ticker=ticker,
            start_test_date=cur_start_s,
            end_test_date=cur_end_s,
            history_padding_days=history_padding_days,
            timeframe=timeframe,
            drop_wide_spread_bars=drop_wide_spread_bars,
        )
        if not df.empty:
            return df
    except Exception as exc:
        alpaca_exc = exc

    try:
        df = _fetch_moex_bars(
            ticker=ticker,
            start_test_date=cur_start_s,
            end_test_date=cur_end_s,
            history_padding_days=history_padding_days,
            timeframe=timeframe,
            drop_wide_spread_bars=drop_wide_spread_bars,
        )
        if not df.empty:
            return df
    except Exception as moex_exc:
        raise RuntimeError(
            "Unable to fetch market data with auto provider selection. "
            f"Alpaca error: {alpaca_exc}; MOEX error: {moex_exc}"
        ) from moex_exc

    return pd.DataFrame()


def fetch_crypto_bars(
    ticker: str,
    start_test_date: str,
    end_test_date: str,
    timeframe: TimeFrame,
    *,
    drop_wide_spread_bars: bool = True,
) -> pd.DataFrame:
    return _fetch_alpaca_crypto_bars_with_429_backoff(
        ticker=ticker,
        start_test_date=start_test_date,
        end_test_date=end_test_date,
        timeframe=timeframe,
        drop_wide_spread_bars=drop_wide_spread_bars,
    )
