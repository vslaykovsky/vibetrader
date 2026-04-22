import pandas as pd
import pytest

from application.services.scale_utils import (
    floor_ts_to_scale,
    is_finer_or_equal,
    normalize_scale,
    scale_divides,
    scale_minutes,
)


def test_scale_utils_helpers_basic():
    assert normalize_scale(" 1H ") == "1h"
    with pytest.raises(ValueError):
        normalize_scale("5m")
    assert scale_minutes("15m") == 15
    assert scale_minutes("1d") == 60 * 24
    assert is_finer_or_equal("1h", "1d") is True
    assert is_finer_or_equal("1d", "1h") is False
    assert scale_divides("1h", "1d") is True
    assert scale_divides("4h", "1d") is True
    assert scale_divides("1h", "4h") is True
    assert scale_divides("15m", "1h") is True
    assert floor_ts_to_scale(pd.Timestamp("2024-01-02 13:37:00Z"), "1h") == pd.Timestamp(
        "2024-01-02 13:00:00Z"
    )
    assert floor_ts_to_scale(pd.Timestamp("2024-01-02 13:37:00Z"), "1d") == pd.Timestamp(
        "2024-01-02 00:00:00Z"
    )
