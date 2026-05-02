from services.agent import _coding_agent_usage_limit_error, redact_secret_json_values_for_user


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


def test_coding_agent_usage_limit_error():
    assert _coding_agent_usage_limit_error(
        "ERROR: You've hit your usage limit. To get more access now, send a request to your admin or try again at 3:55 PM."
    )
    assert not _coding_agent_usage_limit_error("ERROR: syntax check failed")
