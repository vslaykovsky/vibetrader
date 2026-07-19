import json

from services.agent import (
    _hyperopt_ui_line_to_status_text,
    _make_throttled_hyperopt_progress_handler,
)


def test_hyperopt_status_text_includes_step_time_and_eta():
    raw = json.dumps(
        {
            "hyperopt_ui": True,
            "event": "trial",
            "trial": 2,
            "n_trials": 5,
            "objective_metric": "total_return",
            "outcome": "completed",
            "trial_value": 1.23456,
            "best_value": 2.0,
            "seconds_per_step": 12.5,
            "eta_seconds": 37.5,
        }
    )

    assert (
        _hyperopt_ui_line_to_status_text(raw)
        == "Hyperopt · trial 2/5 · total_return=1.235 · best total_return=2 · 12.5s/step · ETA 37.5s"
    )


def test_hyperopt_progress_throttles_trials_but_keeps_completion():
    messages: list[str] = []
    handle = _make_throttled_hyperopt_progress_handler(
        messages.append,
        interval_seconds=60,
    )
    handle(
        json.dumps(
            {
                "hyperopt_ui": True,
                "event": "walk_forward_start",
                "n_folds": 50,
                "n_trials": 30,
                "objective_metric": "total_return",
            }
        )
    )
    for trial in range(1, 31):
        handle(
            json.dumps(
                {
                    "hyperopt_ui": True,
                    "event": "trial",
                    "fold": 1,
                    "n_folds": 50,
                    "trial": trial,
                    "n_trials": 30,
                    "objective_metric": "total_return",
                    "outcome": "completed",
                    "trial_value": 1.0,
                    "best_value": 1.0,
                }
            )
        )
    handle(
        json.dumps(
            {
                "hyperopt_ui": True,
                "event": "walk_forward_done",
                "n_folds": 50,
                "objective_metric": "total_return",
                "metrics": {"total_return": 2.0},
            }
        )
    )

    assert messages == [
        "Walk-forward · 50 folds · 30 trials/fold · total_return",
        "Walk-forward · done · 50 folds · OOS total_return=2",
    ]
