import pytest
from alpaca.data.timeframe import TimeFrame

from strategies import utils


def test_period_from_timeframe():
    assert utils.period_from_timeframe(TimeFrame.Day) == 24
    assert utils.period_from_timeframe(TimeFrame.Hour) == 60
    assert utils.period_from_timeframe(TimeFrame.Week) == 7
    assert utils.period_from_timeframe(TimeFrame.Minute) == 1
    with pytest.raises(ValueError):
        utils.period_from_timeframe(TimeFrame.Month)
