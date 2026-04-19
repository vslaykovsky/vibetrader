import pytest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from strategies import utils


def test_period_from_timeframe():
    assert utils.period_from_timeframe(TimeFrame.Day) == 24
    assert utils.period_from_timeframe(TimeFrame.Hour) == 60
    assert utils.period_from_timeframe(TimeFrame(4, TimeFrameUnit.Hour)) == 240
    assert utils.period_from_timeframe(TimeFrame.Week) == 7
    assert utils.period_from_timeframe(TimeFrame.Minute) == 1
    assert utils.period_from_timeframe(TimeFrame(15, TimeFrameUnit.Minute)) == 15
    with pytest.raises(ValueError):
        utils.period_from_timeframe(TimeFrame.Month)
