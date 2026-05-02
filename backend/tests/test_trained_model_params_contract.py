from strategies_v2.utils import StrategyInput, StrategyOutput


def test_trained_model_params_contract_round_trips():
    raw_input = {
        "unixtime": 0,
        "points": [
            {
                "kind": "trained_model_params",
                "name": "xgboost_signal",
                "data": {"weights": [0.1, 0.2], "threshold": 0.6},
            }
        ],
    }
    raw_output = [
        {
            "kind": "trained_model_params",
            "name": "xgboost_signal",
            "data": {"weights": [0.1, 0.2], "threshold": 0.6},
        }
    ]

    parsed_input = StrategyInput.model_validate(raw_input)
    parsed_output = StrategyOutput.model_validate(raw_output)

    assert parsed_input.model_dump(mode="json") == raw_input
    assert parsed_output.model_dump(mode="json") == raw_output
