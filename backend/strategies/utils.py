import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass
import pandas as pd

from alpaca.data.enums import CryptoFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
PARAMS_PATH = OUTPUT_DIR / "params.json"
DATA_PATH = OUTPUT_DIR / "data.json"


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


def timeframe_from_string(value: str) -> TimeFrame:
    normalized = str(value).strip().lower()
    if normalized in {"1day", "day", "1d"}:
        return TimeFrame.Day
    if normalized in {"1hour", "hour", "1h"}:
        return TimeFrame.Hour
    if normalized in {"1min", "1minute", "minute", "1m"}:
        return TimeFrame.Minute
    raise ValueError(f"Unsupported timeframe: {value}")


def normalize_crypto_symbol(ticker: str) -> str:
    t = ticker.strip().upper()
    if "/" in t:
        return t
    if len(t) > 3 and t.endswith("USD"):
        return f"{t[:-3]}/USD"
    return t


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


def _alpaca_keys() -> tuple[str, str]:
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set")
    return api_key, secret_key


def fetch_stock_bars(
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
    end = datetime.fromisoformat(end_test_date)
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
    end = pd.Timestamp(end_test_date, tz="UTC") + pd.Timedelta(days=1)
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
