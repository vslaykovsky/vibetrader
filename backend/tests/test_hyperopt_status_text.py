import json

from services.agent import _hyperopt_ui_line_to_status_text


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
