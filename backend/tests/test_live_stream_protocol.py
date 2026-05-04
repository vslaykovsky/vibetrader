from types import SimpleNamespace

from application.schemas.live_stream import build_live_stream_snapshot, live_stream_patch_from_event


def _event(event_id, kind, payload, unixtime=1000):
    return SimpleNamespace(
        id=event_id,
        run_id="run-1",
        kind=kind,
        unixtime=unixtime,
        payload=payload,
    )


def test_build_live_stream_snapshot_serializes_chart_patches():
    rows = [
        _event(
            1,
            "startup",
            {
                "startup": [
                    {
                        "kind": "ticker_subscription",
                        "id": "price",
                        "ticker": "SPY",
                        "scale": "1m",
                    },
                    {
                        "kind": "indicator_subscription",
                        "indicator": {
                            "kind": "ema",
                            "id": "fast_ema",
                            "ticker": "SPY",
                            "scale": "1m",
                            "period": 9,
                        },
                    },
                    {
                        "kind": "indicator_series_catalog",
                        "series": [{"name": "edge", "description": "model edge"}],
                    },
                ]
            },
        ),
        _event(2, "status", {"status": "running", "ticker": "SPY", "base_scale": "1m"}, 1001),
        _event(
            3,
            "bar",
            {
                "kind": "ohlc",
                "id": "price",
                "ticker": "SPY",
                "ohlc": {"open": 10, "high": 12, "low": 9, "close": 11, "volume": 100},
                "closed": True,
            },
            1002,
        ),
        _event(
            4,
            "indicator_in",
            {"kind": "indicator", "id": "fast_ema", "name": "ema", "value": 10.5, "closed": True},
            1002,
        ),
        _event(5, "indicator_out", {"kind": "indicator", "name": "edge", "value": 0.7}, 1002),
        _event(
            6,
            "portfolio",
            {
                "kind": "portfolio",
                "cash": 5000,
                "equity": 12000,
                "buying_power": 5000,
                "positions": [
                    {
                        "ticker": "SPY",
                        "order_type": "long",
                        "deposit_ratio": 0.25,
                        "volume_weighted_avg_entry_price": 10,
                    }
                ],
            },
            1002,
        ),
        _event(
            7,
            "portfolio",
            {
                "kind": "portfolio",
                "cash": 12000,
                "equity": 12000,
                "buying_power": 12000,
                "positions": [],
            },
            1060,
        ),
        _event(
            8,
            "order_signal",
            {
                "kind": "market_order",
                "ticker": "SPY",
                "direction": "buy",
                "deposit_ratio": 0.5,
                "notional": 6000,
                "client_order_id": "client-1",
                "status": "rejected",
                "short_explanation": "crossed above EMA",
                "alpaca_error_message": "insufficient buying power",
                "alpaca_error_code": "40310000",
                "alpaca_status_code": 403,
            },
            1002,
        ),
        _event(9, "live_boundary", {"label": "Live trading starts"}, 1050),
    ]

    snapshot, ctx = build_live_stream_snapshot("run-1", rows)
    assert snapshot.model_dump()["data"] == {
        "last_seq": 9,
        "series": [
            {
                "chart_id": "ohlcv",
                "series_id": "ohlcv:price",
                "source": "ohlcv",
                "label": "SPY",
                "name": "",
                "ticker": "SPY",
                "scale": "1m",
                "description": "",
            },
            {
                "chart_id": "input_indicators",
                "series_id": "input:fast_ema:ema",
                "source": "input_indicator",
                "label": "fast_ema:ema",
                "name": "ema",
                "ticker": "SPY",
                "scale": "1m",
                "description": "",
            },
            {
                "chart_id": "output_indicators",
                "series_id": "output:edge",
                "source": "output_indicator",
                "label": "output:edge",
                "name": "edge",
                "ticker": "",
                "scale": "",
                "description": "model edge",
            },
            {
                "chart_id": "positions",
                "series_id": "position:SPY",
                "source": "position",
                "label": "SPY position value",
                "name": "",
                "ticker": "SPY",
                "scale": "",
                "description": "",
            },
        ],
        "bars": [
            {
                "chart_id": "ohlcv",
                "series_id": "ohlcv:price",
                "label": "SPY",
                "ticker": "SPY",
                "scale": "1m",
                "time": 1002,
                "open": 10.0,
                "high": 12.0,
                "low": 9.0,
                "close": 11.0,
                "volume": 100.0,
                "closed": True,
            }
        ],
        "indicators": [
            {
                "chart_id": "input_indicators",
                "series_id": "input:fast_ema:ema",
                "source": "input",
                "label": "fast_ema:ema",
                "name": "ema",
                "time": 1002,
                "value": 10.5,
                "closed": True,
                "description": "",
            },
            {
                "chart_id": "output_indicators",
                "series_id": "output:edge",
                "source": "output",
                "label": "output:edge",
                "name": "edge",
                "time": 1002,
                "value": 0.7,
                "closed": True,
                "description": "model edge",
            },
        ],
        "positions": [
            {
                "chart_id": "positions",
                "time": 1002,
                "equity": 12000.0,
                "positions": [
                    {
                        "ticker": "SPY",
                        "order_type": "long",
                        "deposit_ratio": 0.25,
                        "volume_weighted_avg_entry_price": 10.0,
                        "value": 3000.0,
                    }
                ],
            },
            {
                "chart_id": "positions",
                "time": 1060,
                "equity": 12000.0,
                "positions": [
                    {
                        "ticker": "SPY",
                        "order_type": "",
                        "deposit_ratio": None,
                        "volume_weighted_avg_entry_price": None,
                        "value": 0.0,
                    }
                ],
            }
        ],
        "trades": [
            {
                "time": 1002,
                "ticker": "SPY",
                "direction": "buy",
                "action": "",
                "label": "",
                "price": None,
                "qty": None,
                "value_usd": 6000.0,
                "deposit_ratio": 0.5,
                "position_before_order": None,
                "position_after_order_filled": None,
                "alpaca_order_id": "",
                "client_order_id": "client-1",
                "status": "rejected",
                "comment": (
                    "crossed above EMA; Alpaca rejected order: insufficient buying power "
                    "(code 40310000, HTTP 403)"
                ),
            }
        ],
        "annotations": [
            {
                "time": 1050,
                "kind": "live_start",
                "label": "Live trading starts",
            }
        ],
        "status": {
            "status": "running",
            "message": "",
            "ticker": "SPY",
            "base_scale": "1m",
        },
    }

    patch = live_stream_patch_from_event(
        _event(
            10,
            "bar",
            {
                "kind": "ohlc",
                "id": "price",
                "ticker": "SPY",
                "ohlc": {"open": 11, "high": 13, "low": 10, "close": 12, "volume": 110},
            },
            1060,
        ),
        ctx,
    )
    assert patch.model_dump() == {
        "kind": "bar",
        "seq": 10,
        "run_id": "run-1",
        "unixtime": 1060,
        "data": {
            "chart_id": "ohlcv",
            "series_id": "ohlcv:price",
            "label": "SPY",
            "ticker": "SPY",
            "scale": "1m",
            "time": 1060,
            "open": 11.0,
            "high": 13.0,
            "low": 10.0,
            "close": 12.0,
            "volume": 110.0,
            "closed": True,
        },
    }
