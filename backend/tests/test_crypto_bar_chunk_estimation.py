import pandas as pd
from alpaca.data.timeframe import TimeFrame

from strategies import utils


def test_estimate_crypto_bars_between():
    tf = TimeFrame.Minute
    a = pd.Timestamp("2024-01-01", tz="UTC")
    b = pd.Timestamp("2024-01-01 01:00:00", tz="UTC")
    assert utils._estimate_crypto_bars_between(a, a, tf) == 0
    assert utils._estimate_crypto_bars_between(a, b, tf) == 60


def test_crypto_largest_chunk_end_exclusive():
    tf = TimeFrame.Minute
    cur = pd.Timestamp("2020-01-01", tz="UTC")
    end = pd.Timestamp("2021-06-01", tz="UTC")
    budget = 100_000
    chunk_end = utils._crypto_largest_chunk_end_exclusive(cur, end, tf, budget)
    assert cur < chunk_end <= end
    assert utils._estimate_crypto_bars_between(cur, chunk_end, tf) <= budget
