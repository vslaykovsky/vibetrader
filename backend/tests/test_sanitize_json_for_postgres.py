import math

from services.agent import sanitize_json_for_postgres


def test_sanitize_json_for_postgres():
    inp = {
        "a": 1,
        "b": float("nan"),
        "c": float("inf"),
        "d": float("-inf"),
        "e": [1.0, math.nan, {"x": math.inf}],
        "f": "ok",
        "g": True,
    }
    assert sanitize_json_for_postgres(inp) == {
        "a": 1,
        "b": None,
        "c": None,
        "d": None,
        "e": [1.0, None, {"x": None}],
        "f": "ok",
        "g": True,
    }
