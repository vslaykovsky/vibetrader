from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field


class LiveSeriesMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart_id: str
    series_id: str
    source: Literal["ohlcv", "input_indicator", "output_indicator", "position"]
    label: str
    name: str = ""
    ticker: str = ""
    scale: str = ""
    description: str = ""


class LiveBarPatchData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart_id: str = "ohlcv"
    series_id: str
    label: str
    ticker: str
    scale: str = ""
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    closed: bool = True


class LiveIndicatorPatchData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart_id: str
    series_id: str
    source: Literal["input", "output"]
    label: str
    name: str
    time: int
    value: float
    closed: bool = True
    description: str = ""


class LiveTradePatchData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: int
    ticker: str = ""
    direction: str = ""
    action: str = ""
    label: str = ""
    price: float | None = None
    qty: float | None = None
    value_usd: float | None = None
    deposit_ratio: float | None = None
    position_before_order: float | None = None
    position_after_order_filled: float | None = None
    alpaca_order_id: str = ""
    client_order_id: str = ""
    status: str = ""
    comment: str = ""


class LivePositionPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    order_type: str = ""
    deposit_ratio: float | None = None
    volume_weighted_avg_entry_price: float | None = None
    value: float


class LivePositionPatchData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart_id: str = "positions"
    time: int
    equity: float | None = None
    positions: list[LivePositionPoint]


class LiveStatusPatchData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    message: str = ""
    ticker: str = ""
    base_scale: str = ""


class LiveAnnotationPatchData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: int
    kind: Literal["live_start"] = "live_start"
    label: str = "Live trading starts"


class LiveSnapshotData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_seq: int
    series: list[LiveSeriesMeta]
    bars: list[LiveBarPatchData]
    indicators: list[LiveIndicatorPatchData]
    positions: list[LivePositionPatchData]
    trades: list[LiveTradePatchData]
    annotations: list[LiveAnnotationPatchData] = Field(default_factory=list)
    status: LiveStatusPatchData | None = None


class LiveSnapshotEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["snapshot"] = "snapshot"
    seq: int
    run_id: str
    unixtime: int
    data: LiveSnapshotData


class LiveBarPatchEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["bar"] = "bar"
    seq: int
    run_id: str
    unixtime: int
    data: LiveBarPatchData


class LiveIndicatorPatchEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["indicator"] = "indicator"
    seq: int
    run_id: str
    unixtime: int
    data: LiveIndicatorPatchData


class LiveTradePatchEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["trade"] = "trade"
    seq: int
    run_id: str
    unixtime: int
    data: LiveTradePatchData


class LivePositionPatchEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["position"] = "position"
    seq: int
    run_id: str
    unixtime: int
    data: LivePositionPatchData


class LiveStatusPatchEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["status"] = "status"
    seq: int
    run_id: str
    unixtime: int
    data: LiveStatusPatchData


class LiveAnnotationPatchEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["annotation"] = "annotation"
    seq: int
    run_id: str
    unixtime: int
    data: LiveAnnotationPatchData


LivePatchEvent = (
    LiveBarPatchEvent
    | LiveIndicatorPatchEvent
    | LiveTradePatchEvent
    | LivePositionPatchEvent
    | LiveStatusPatchEvent
    | LiveAnnotationPatchEvent
)
LiveStreamEvent = LiveSnapshotEvent | LivePatchEvent


@dataclass
class LiveStreamContext:
    ticker_subs: dict[str, dict[str, str]] = field(default_factory=dict)
    indicator_subs: dict[str, dict[str, str]] = field(default_factory=dict)
    catalog: dict[str, str] = field(default_factory=dict)
    position_tickers: set[str] = field(default_factory=set)
    status: LiveStatusPatchData | None = None


def build_live_stream_snapshot(
    run_id: str, rows: Sequence[Any]
) -> tuple[LiveSnapshotEvent, LiveStreamContext]:
    ctx = LiveStreamContext()
    bar_by_key: dict[tuple[str, int], LiveBarPatchData] = {}
    indicator_by_key: dict[tuple[str, int], LiveIndicatorPatchData] = {}
    position_by_time: dict[int, LivePositionPatchData] = {}
    annotation_by_key: dict[tuple[str, int], LiveAnnotationPatchData] = {}
    trades: list[LiveTradePatchData] = []
    last_seq = 0

    for row in rows:
        last_seq = max(last_seq, _seq(row))
        patch = live_stream_patch_from_event(row, ctx)
        if patch is None:
            continue
        if patch.kind == "bar":
            bar_by_key[(patch.data.series_id, patch.data.time)] = patch.data
        elif patch.kind == "indicator":
            indicator_by_key[(patch.data.series_id, patch.data.time)] = patch.data
        elif patch.kind == "position":
            position_by_time[patch.data.time] = patch.data
        elif patch.kind == "trade":
            trades.append(patch.data)
        elif patch.kind == "annotation":
            annotation_by_key[(patch.data.kind, patch.data.time)] = patch.data

    bars = sorted(bar_by_key.values(), key=lambda x: (x.series_id, x.time))
    indicators = sorted(indicator_by_key.values(), key=lambda x: (x.series_id, x.time))
    positions = sorted(position_by_time.values(), key=lambda x: x.time)
    annotations = sorted(annotation_by_key.values(), key=lambda x: (x.time, x.kind))
    snapshot = LiveSnapshotEvent(
        seq=last_seq,
        run_id=run_id,
        unixtime=int(time.time()),
        data=LiveSnapshotData(
            last_seq=last_seq,
            series=_series_meta(ctx),
            bars=bars,
            indicators=indicators,
            positions=positions,
            trades=trades,
            annotations=annotations,
            status=ctx.status,
        ),
    )
    return snapshot, ctx


def live_stream_patch_from_event(row: Any, ctx: LiveStreamContext) -> LivePatchEvent | None:
    kind = str(getattr(row, "kind", "") or "")
    payload = _payload(row)
    if kind == "startup":
        _consume_startup(ctx, payload)
        return None
    if kind == "status":
        return _status_event(row, ctx, payload)
    if kind == "bar":
        return _bar_event(row, ctx, payload)
    if kind == "indicator_in":
        return _indicator_event(row, ctx, payload, source="input")
    if kind == "indicator_out":
        return _indicator_event(row, ctx, payload, source="output")
    if kind == "portfolio":
        return _position_event(row, ctx, payload)
    if kind in {"order_signal", "order_update"}:
        return _trade_event(row, ctx, payload)
    if kind == "live_boundary":
        return _annotation_event(row, ctx, payload)
    return None


def _payload(row: Any) -> dict[str, Any]:
    payload = getattr(row, "payload", {}) or {}
    return payload if isinstance(payload, dict) else {}


def _seq(row: Any) -> int:
    try:
        return int(getattr(row, "id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _event_unixtime(row: Any) -> int:
    try:
        raw = getattr(row, "unixtime", None)
        if raw is not None:
            return int(raw)
    except (TypeError, ValueError):
        pass
    return int(time.time())


def _run_id(row: Any) -> str:
    return str(getattr(row, "run_id", "") or "")


def _str_field(data: dict[str, Any], key: str) -> str:
    raw = data.get(key)
    return raw.strip() if isinstance(raw, str) else ""


def _float_field(data: dict[str, Any], key: str) -> float | None:
    try:
        value = float(data.get(key))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _series_id(prefix: str, *parts: str) -> str:
    clean = [str(x).strip() for x in parts if str(x).strip()]
    return prefix + ":" + ":".join(clean) if clean else prefix


def _consume_startup(ctx: LiveStreamContext, payload: dict[str, Any]) -> None:
    startup = payload.get("startup")
    if not isinstance(startup, list):
        return
    indicator_i = 0
    ticker_i = 0
    for item in startup:
        if not isinstance(item, dict):
            continue
        item_kind = str(item.get("kind") or "")
        if item_kind == "ticker_subscription":
            sid = _str_field(item, "id") or f"ticker_{ticker_i}"
            ticker_i += 1
            ticker = _str_field(item, "ticker")
            ctx.ticker_subs[sid] = {
                "ticker": ticker,
                "scale": _str_field(item, "scale"),
            }
            if ticker:
                ctx.position_tickers.add(ticker)
        elif item_kind == "indicator_subscription":
            indicator = item.get("indicator")
            if not isinstance(indicator, dict):
                continue
            ind_kind = _str_field(indicator, "kind") or "indicator"
            sid = _str_field(indicator, "id") or f"{ind_kind}_{indicator_i}"
            indicator_i += 1
            ctx.indicator_subs[sid] = {
                "kind": ind_kind,
                "ticker": _str_field(indicator, "ticker"),
                "scale": _str_field(indicator, "scale"),
                "outputs": ",".join(_indicator_outputs(indicator)),
            }
        elif item_kind == "indicator_series_catalog":
            series = item.get("series")
            if isinstance(series, list):
                for row in series:
                    if not isinstance(row, dict):
                        continue
                    name = _str_field(row, "name")
                    if name:
                        ctx.catalog[name] = _str_field(row, "description")


def _indicator_outputs(indicator: dict[str, Any]) -> list[str]:
    outputs = indicator.get("outputs")
    if isinstance(outputs, list):
        return [str(x) for x in outputs if str(x).strip()]
    kind = _str_field(indicator, "kind")
    if kind in {"sma", "ema", "rsi", "atr"}:
        return [kind]
    if kind == "macd":
        return ["macd", "signal", "histogram"]
    if kind == "bb":
        return ["bb_middle", "bb_upper", "bb_lower"]
    if kind == "stochastic":
        return ["stoch_k", "stoch_d"]
    if kind == "fibonacci":
        return ["fib_0p236", "fib_0p382", "fib_0p5", "fib_0p618", "fib_0p786"]
    return []


def _series_meta(ctx: LiveStreamContext) -> list[LiveSeriesMeta]:
    out: list[LiveSeriesMeta] = []
    for sid, row in sorted(ctx.ticker_subs.items()):
        ticker = row.get("ticker", "")
        out.append(
            LiveSeriesMeta(
                chart_id="ohlcv",
                series_id=_series_id("ohlcv", sid),
                source="ohlcv",
                label=ticker or sid,
                ticker=ticker,
                scale=row.get("scale", ""),
            )
        )
    for sid, row in sorted(ctx.indicator_subs.items()):
        for name in [x for x in row.get("outputs", "").split(",") if x]:
            label = sid if sid == name else f"{sid}:{name}"
            out.append(
                LiveSeriesMeta(
                    chart_id="input_indicators",
                    series_id=_series_id("input", sid, name),
                    source="input_indicator",
                    label=label,
                    name=name,
                    ticker=row.get("ticker", ""),
                    scale=row.get("scale", ""),
                )
            )
    for name, description in sorted(ctx.catalog.items()):
        out.append(
            LiveSeriesMeta(
                chart_id="output_indicators",
                series_id=_series_id("output", name),
                source="output_indicator",
                label=f"output:{name}",
                name=name,
                description=description,
            )
        )
    for ticker in sorted(ctx.position_tickers):
        out.append(
            LiveSeriesMeta(
                chart_id="positions",
                series_id=_series_id("position", ticker),
                source="position",
                label=f"{ticker} position value",
                ticker=ticker,
            )
        )
    return out


def _status_event(
    row: Any, ctx: LiveStreamContext, payload: dict[str, Any]
) -> LiveStatusPatchEvent | None:
    status = _str_field(payload, "status")
    if not status:
        return None
    data = LiveStatusPatchData(
        status=status,
        message=_str_field(payload, "message"),
        ticker=_str_field(payload, "ticker"),
        base_scale=_str_field(payload, "base_scale"),
    )
    ctx.status = data
    return LiveStatusPatchEvent(
        seq=_seq(row),
        run_id=_run_id(row),
        unixtime=_event_unixtime(row),
        data=data,
    )


def _annotation_event(
    row: Any, ctx: LiveStreamContext, payload: dict[str, Any]
) -> LiveAnnotationPatchEvent | None:
    event_time = _event_unixtime(row)
    data = LiveAnnotationPatchData(
        time=event_time,
        label=_str_field(payload, "label") or "Live trading starts",
    )
    return LiveAnnotationPatchEvent(
        seq=_seq(row),
        run_id=_run_id(row),
        unixtime=event_time,
        data=data,
    )


def _bar_event(
    row: Any, ctx: LiveStreamContext, payload: dict[str, Any]
) -> LiveBarPatchEvent | None:
    ohlc = payload.get("ohlc")
    if not isinstance(ohlc, dict):
        return None
    open_v = _float_field(ohlc, "open")
    high_v = _float_field(ohlc, "high")
    low_v = _float_field(ohlc, "low")
    close_v = _float_field(ohlc, "close")
    if open_v is None or high_v is None or low_v is None or close_v is None:
        return None
    sid = _str_field(payload, "id") or "price"
    meta = ctx.ticker_subs.get(sid, {})
    ticker = _str_field(payload, "ticker") or meta.get("ticker", "")
    data = LiveBarPatchData(
        series_id=_series_id("ohlcv", sid),
        label=ticker or sid,
        ticker=ticker,
        scale=meta.get("scale", ""),
        time=_event_unixtime(row),
        open=open_v,
        high=high_v,
        low=low_v,
        close=close_v,
        volume=_float_field(ohlc, "volume"),
        closed=bool(payload.get("closed", True)),
    )
    return LiveBarPatchEvent(
        seq=_seq(row),
        run_id=_run_id(row),
        unixtime=data.time,
        data=data,
    )


def _indicator_event(
    row: Any, ctx: LiveStreamContext, payload: dict[str, Any], *, source: Literal["input", "output"]
) -> LiveIndicatorPatchEvent | None:
    name = _str_field(payload, "name")
    value = _float_field(payload, "value")
    if not name or value is None:
        return None
    if source == "input":
        raw_id = _str_field(payload, "id") or "indicator"
        series_id = _series_id("input", raw_id, name)
        label = raw_id if raw_id == name else f"{raw_id}:{name}"
        chart_id = "input_indicators"
        description = ""
    else:
        series_id = _series_id("output", name)
        label = f"output:{name}"
        chart_id = "output_indicators"
        description = ctx.catalog.get(name, "")
    data = LiveIndicatorPatchData(
        chart_id=chart_id,
        series_id=series_id,
        source=source,
        label=label,
        name=name,
        time=_event_unixtime(row),
        value=value,
        closed=bool(payload.get("closed", True)),
        description=description,
    )
    return LiveIndicatorPatchEvent(
        seq=_seq(row),
        run_id=_run_id(row),
        unixtime=data.time,
        data=data,
    )


def _order_comment(payload: dict[str, Any]) -> str:
    comment = (
        _str_field(payload, "short_explanation")
        or _str_field(payload, "reason")
        or _str_field(payload, "comment")
    )
    if not comment:
        event = _str_field(payload, "broker_event") or _str_field(payload, "event")
        comment = f"Alpaca {event}" if event else "strategy signal"
    error = _str_field(payload, "alpaca_error_message") or _str_field(payload, "error")
    if not error:
        return comment
    code = _str_field(payload, "alpaca_error_code")
    raw_status_code = payload.get("alpaca_status_code")
    status_code = _str_field(payload, "alpaca_status_code")
    if not status_code and raw_status_code is not None:
        status_code = str(raw_status_code).strip()
    detail = f"Alpaca rejected order: {error}"
    suffix = ", ".join(
        x
        for x in (
            f"code {code}" if code else "",
            f"HTTP {status_code}" if status_code else "",
        )
        if x
    )
    if suffix:
        detail = f"{detail} ({suffix})"
    if detail in comment:
        return comment
    return f"{comment}; {detail}" if comment else detail


def _order_value_usd(payload: dict[str, Any]) -> float | None:
    value = _float_field(payload, "value_usd")
    if value is not None:
        return abs(value)
    notional = _float_field(payload, "notional")
    if notional is not None:
        return abs(notional)
    price = _float_field(payload, "price")
    qty = _float_field(payload, "qty")
    if price is not None and qty is not None:
        return abs(price * qty)
    filled_avg_price = _float_field(payload, "filled_avg_price")
    filled_qty = _float_field(payload, "filled_qty")
    if filled_avg_price is not None and filled_qty is not None:
        return abs(filled_avg_price * filled_qty)
    return None


def _trade_event(
    row: Any, ctx: LiveStreamContext, payload: dict[str, Any]
) -> LiveTradePatchEvent | None:
    comment = _order_comment(payload)
    data = LiveTradePatchData(
        time=_event_unixtime(row),
        ticker=_str_field(payload, "ticker"),
        direction=_str_field(payload, "direction"),
        action=_str_field(payload, "action"),
        label=_str_field(payload, "label"),
        price=_float_field(payload, "price"),
        qty=_float_field(payload, "qty"),
        value_usd=_order_value_usd(payload),
        deposit_ratio=_float_field(payload, "deposit_ratio"),
        position_before_order=_float_field(payload, "position_before_order"),
        position_after_order_filled=_float_field(payload, "position_after_order_filled"),
        alpaca_order_id=_str_field(payload, "alpaca_order_id"),
        client_order_id=_str_field(payload, "client_order_id"),
        status=_str_field(payload, "status"),
        comment=comment,
    )
    if data.ticker:
        ctx.position_tickers.add(data.ticker)
    return LiveTradePatchEvent(
        seq=_seq(row),
        run_id=_run_id(row),
        unixtime=data.time,
        data=data,
    )


def _position_event(row: Any, ctx: LiveStreamContext, payload: dict[str, Any]) -> LivePositionPatchEvent:
    event_time = _event_unixtime(row)
    equity = _float_field(payload, "equity")
    positions_raw = payload.get("positions")
    positions: list[LivePositionPoint] = []
    if isinstance(positions_raw, list):
        for item in positions_raw:
            if not isinstance(item, dict):
                continue
            ticker = _str_field(item, "ticker")
            if not ticker:
                continue
            order_type = _str_field(item, "order_type")
            deposit_ratio = _float_field(item, "deposit_ratio")
            sign = -1.0 if order_type == "short" else 1.0
            value = sign * float(deposit_ratio or 0.0) * float(equity or 0.0)
            positions.append(
                LivePositionPoint(
                    ticker=ticker,
                    order_type=order_type,
                    deposit_ratio=deposit_ratio,
                    volume_weighted_avg_entry_price=_float_field(
                        item, "volume_weighted_avg_entry_price"
                    ),
                    value=value,
                )
            )
            ctx.position_tickers.add(ticker)
    seen = {p.ticker for p in positions}
    for ticker in sorted(ctx.position_tickers - seen):
        positions.append(LivePositionPoint(ticker=ticker, value=0.0))
    data = LivePositionPatchData(time=event_time, equity=equity, positions=positions)
    return LivePositionPatchEvent(
        seq=_seq(row),
        run_id=_run_id(row),
        unixtime=event_time,
        data=data,
    )
