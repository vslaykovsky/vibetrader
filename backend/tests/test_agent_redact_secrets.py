from services.agent import redact_secret_json_values_for_user


def test_redact_secret_json_values_for_user():
    data = {
        "a": 1,
        "api_key": "x",
        "OPENAI_API_KEY": "sk",
        "nested": {"ALPACA_SECRET_KEY": "y", "openrouter-api-key": "z", "ok": 2},
        "items": [{"password": "p", "POSTGRES_PASSWORD": "q"}, 3],
    }
    assert redact_secret_json_values_for_user(data) == {
        "a": 1,
        "api_key": "x",
        "OPENAI_API_KEY": "",
        "nested": {"ALPACA_SECRET_KEY": "", "openrouter-api-key": "", "ok": 2},
        "items": [{"password": "p", "POSTGRES_PASSWORD": ""}, 3],
    }
    canvas = {
        "output": {
            "params.json": '{\n  "ticker": "SPY",\n  "OPENAI_API_KEY": "secret"\n}',
        }
    }
    out = redact_secret_json_values_for_user(canvas)
    assert '"OPENAI_API_KEY": ""' in out["output"]["params.json"]
    assert "secret" not in out["output"]["params.json"]
    assert "SPY" in out["output"]["params.json"]
