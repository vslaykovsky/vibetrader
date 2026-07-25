from __future__ import annotations

from datetime import date
from functools import lru_cache

import exchange_calendars as xcals

_CRYPTO_SYMBOLS_WITHOUT_SEPARATOR = {
    "AAVEUSD",
    "AVAXUSD",
    "BCHUSD",
    "BTCUSD",
    "DOGEUSD",
    "DOTUSD",
    "ETHUSD",
    "LINKUSD",
    "LTCUSD",
    "SHIBUSD",
    "SOLUSD",
    "UNIUSD",
    "USDCUSD",
    "USDTUSD",
    "XRPUSD",
}


@lru_cache(maxsize=1)
def _xnys_calendar():
    return xcals.get_calendar("XNYS")


def uses_xnys_calendar(
    *,
    ticker: str,
    provider: str | None,
    asset_class: str | None = None,
) -> bool:
    """Return whether a market-data request should follow the NYSE session calendar."""
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider == "moex":
        return False

    normalized_asset = str(asset_class or "").strip().lower()
    if normalized_asset:
        return normalized_asset == "us_equity"

    symbol = str(ticker or "").strip().upper().replace("-", "/")
    if not symbol or "/" in symbol or symbol in _CRYPTO_SYMBOLS_WITHOUT_SEPARATOR:
        return False
    return normalized_provider in {"", "auto", "alpaca"}


def xnys_session_dates(start: date, end: date) -> frozenset[date]:
    if start > end:
        return frozenset()
    sessions = _xnys_calendar().sessions_in_range(start.isoformat(), end.isoformat())
    return frozenset(session.date() for session in sessions)
