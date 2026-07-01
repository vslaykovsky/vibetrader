from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest


STRATEGIES_V2 = Path(__file__).resolve().parents[1] / "strategies_v2"
if str(STRATEGIES_V2) not in sys.path:
    sys.path.insert(0, str(STRATEGIES_V2))

spec = importlib.util.spec_from_file_location(
    "strategies_v2_hyperopt", STRATEGIES_V2 / "hyperopt.py"
)
hyperopt = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(hyperopt)

from utils import ParamsHyperopt  # noqa: E402


def test_walk_forward_folds_are_inclusive_calendar_windows():
    cfg = ParamsHyperopt.model_validate(
        {
            "search_space": {"period": {"type": "int", "low": 2, "high": 5}},
            "mode": "walk_forward",
            "walk_forward": {
                "train_window_days": 30,
                "test_window_days": 10,
                "step_days": 10,
                "oos_total_days": 20,
            },
        }
    )

    folds = hyperopt._walk_forward_folds({"end_date": "2024-12-31"}, cfg)

    assert [
        (
            f["train_start"].isoformat(),
            f["train_end"].isoformat(),
            f["test_start"].isoformat(),
            f["test_end"].isoformat(),
        )
        for f in folds
    ] == [
        ("2024-11-12", "2024-12-11", "2024-12-12", "2024-12-21"),
        ("2024-11-22", "2024-12-21", "2024-12-22", "2024-12-31"),
    ]


def test_stitch_docs_compounds_equity_and_adds_oos_vertical_markers():
    fold_docs = [
        {
            "strategy_name": "Demo",
            "charts": [
                {
                    "type": "lightweight-charts",
                    "title": "Equity curve vs buy & hold",
                    "series": [
                        {
                            "type": "Line",
                            "label": "Strategy equity",
                            "data": [
                                {"time": "2024-01-01", "value": 100.0},
                                {"time": "2024-01-02", "value": 110.0},
                            ],
                        }
                    ],
                }
            ],
        },
        {
            "strategy_name": "Demo",
            "charts": [
                {
                    "type": "lightweight-charts",
                    "title": "Equity curve vs buy & hold",
                    "series": [
                        {
                            "type": "Line",
                            "label": "Strategy equity",
                            "data": [
                                {"time": "2024-01-03", "value": 200.0},
                                {"time": "2024-01-04", "value": 220.0},
                            ],
                        }
                    ],
                }
            ],
        },
    ]

    stitched = hyperopt._stitch_docs(
        fold_docs,
        [
            {"test_start": "2024-01-01", "test_end": "2024-01-02"},
            {"test_start": "2024-01-03", "test_end": "2024-01-04"},
        ],
        100.0,
    )

    equity_chart = stitched["charts"][0]
    assert equity_chart["verticalMarkers"] == [
        {"time": "2024-01-01", "label": "OOS 1", "color": "#f59e0b"},
        {"time": "2024-01-03", "label": "OOS 2", "color": "#f59e0b"},
    ]
    values = [p["value"] for p in equity_chart["series"][0]["data"]]
    assert values == pytest.approx([100.0, 110.0, 110.0, 121.0])


def test_stitch_docs_snaps_oos_marker_to_first_rendered_chart_time():
    fold_docs = [
        {
            "strategy_name": "Demo",
            "charts": [
                {
                    "type": "lightweight-charts",
                    "title": "SPY price (1d)",
                    "series": [
                        {
                            "type": "Candlestick",
                            "label": "SPY",
                            "data": [
                                {
                                    "time": "2024-01-08",
                                    "open": 1.0,
                                    "high": 1.0,
                                    "low": 1.0,
                                    "close": 1.0,
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    ]

    stitched = hyperopt._stitch_docs(
        fold_docs,
        [{"test_start": "2024-01-06", "test_end": "2024-01-12"}],
        100.0,
    )

    assert stitched["charts"][0]["verticalMarkers"] == [
        {"time": "2024-01-08", "label": "OOS 1", "color": "#f59e0b"}
    ]


def test_walk_forward_uses_fresh_timeout_clock_per_fold(monkeypatch):
    cfg = ParamsHyperopt.model_validate(
        {
            "search_space": {"period": {"type": "int", "low": 2, "high": 5}},
            "mode": "walk_forward",
            "n_trials": 1,
            "timeout_seconds": 180,
            "walk_forward": {
                "train_window_days": 30,
                "test_window_days": 10,
                "step_days": 10,
                "oos_total_days": 20,
            },
        }
    )
    base = {
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "initial_deposit": 100.0,
        "scale": "1d",
    }
    study_starts = []

    def fake_run_one_study(**kwargs):
        study_starts.append(kwargs["t0"])
        return (
            {
                **base,
                "period": 2,
                "start_date": kwargs["window_start"].isoformat(),
                "end_date": kwargs["window_end"].isoformat(),
            },
            1.0,
            1,
            1,
        )

    def fake_load_json(path):
        return {
            "strategy_name": "Demo",
            "charts": [
                {
                    "type": "lightweight-charts",
                    "title": "Equity curve vs buy & hold",
                    "series": [
                        {
                            "type": "Line",
                            "label": "Strategy equity",
                            "data": [
                                {"time": "2024-12-12", "value": 100.0},
                                {"time": "2024-12-21", "value": 101.0},
                                {"time": "2024-12-22", "value": 100.0},
                                {"time": "2024-12-31", "value": 101.0},
                            ],
                        }
                    ],
                }
            ],
        }

    monkeypatch.setattr(hyperopt, "_emit_ui", lambda payload: None)
    monkeypatch.setattr(hyperopt, "_run_one_study", fake_run_one_study)
    monkeypatch.setattr(hyperopt, "_save_json", lambda path, obj: None)
    monkeypatch.setattr(hyperopt, "_run_simulation_or_raise", lambda *args, **kwargs: None)
    monkeypatch.setattr(hyperopt, "_load_json", fake_load_json)

    hyperopt._run_walk_forward_mode(
        base=base,
        cfg=cfg,
        active_space=cfg.search_space,
        rng=__import__("random").Random(1),
        t0=time.perf_counter() - 999,
        trial_timeout=1,
    )

    assert len(study_starts) == 2
    assert all(time.perf_counter() - started < 180 for started in study_starts)
