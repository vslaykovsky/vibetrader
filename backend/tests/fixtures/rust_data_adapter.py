from __future__ import annotations

import argparse
import json
from pathlib import Path

import msgpack


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--startup")
    parser.add_argument("--study-startups")
    parser.add_argument("--raw-data")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.study_startups:
        startups = json.loads(Path(args.study_startups).read_text(encoding="utf-8"))
        bars = []
        for index, (unixtime, close) in enumerate(
            (
                (1_704_067_200, 102.0),
                (1_704_153_600, 100.0),
                (1_704_240_000, 103.0),
                (1_704_326_400, 99.0),
            )
        ):
            bars.append(
                {
                    "timestamp_ns": unixtime * 1_000_000_000,
                    "unixtime": unixtime,
                    "chart_time": f"2024-01-0{index + 1}",
                    "open": close - 1.0,
                    "high": close + 1.0,
                    "low": close - 2.0,
                    "close": close,
                    "volume": 1000.0,
                }
            )
        payload = {
            "strategy_name": "Rust optimization integration",
            "primary_ticker": "SPY",
            "base_scale": "1d",
            "simulation_scale": "1d",
            "session": "all",
            "provider": None,
            "initial_deposit": 10_000.0,
            "max_leverage": 1.0,
            "max_padding_days": 30,
            "index_tz_aware": True,
            "bars": bars,
            "trial_windows": [
                {"warmup_start": 0, "simulation_start": 0, "simulation_end": 3}
                for _ in startups
            ],
        }
        Path(args.output).write_bytes(msgpack.packb(payload, use_bin_type=True))
        return
    if not args.startup:
        parser.error("--startup is required outside study preparation")
    events = []
    for index, (unixtime, close) in enumerate(
        ((1_704_153_600, 101.0), (1_704_240_000, 102.0))
    ):
        events.append(
            {
                "unixtime": unixtime,
                "points": [
                    {
                        "kind": "ohlc",
                        "id": "price",
                        "ticker": "SPY",
                        "ohlc": {
                            "open": close - 1.0,
                            "high": close + 1.0,
                            "low": close - 2.0,
                            "close": close,
                            "volume": 1000.0,
                        },
                        "closed": True,
                    }
                ],
                "fills": {"SPY": close},
                "marks": {"SPY": close},
                "invoke_strategy": True,
                "record_equity": True,
                "base_close": True,
                "chart_time": f"2024-01-0{index + 2}",
                "benchmark_close": close,
                "base_row": index,
                "mark_before_input": False,
            }
        )
    payload = {
        "strategy_name": "Rust integration",
        "tickers": ["SPY"],
        "base_scale": "1d",
        "simulation_scale": "1d",
        "primary_ticker": "SPY",
        "initial_deposit": 10_000.0,
        "max_leverage": 1.0,
        "multi_ticker": False,
        "total_units": 2,
        "subscription_charts": [
            {
                "type": "lightweight-charts",
                "title": "SPY price (1d)",
                "description": "",
                "series": [
                    {
                        "type": "Candlestick",
                        "label": "SPY",
                        "options": {},
                        "data": [],
                    }
                ],
            }
        ],
        "events": events,
    }
    Path(args.output).write_bytes(msgpack.packb(payload, use_bin_type=True))


if __name__ == "__main__":
    main()
