from datetime import date, timedelta
from pathlib import Path

from application.services.simulation_limits import (
    CHUNK_BAR_BUDGET,
    estimate_source_bar_count,
    plan_display_bars_fetch_chunks,
    read_strategy_scale,
    simulation_start_validation_error,
)


def test_estimate_daily_small_range():
    n = estimate_source_bar_count(date(2024, 1, 1), date(2024, 1, 10), "1d")
    assert n == 10


def test_estimate_minute_large_range():
    n = estimate_source_bar_count(date(2024, 1, 1), date(2024, 6, 30), "1m")
    assert n > 100_000


def test_validation_accepts_long_range_fine_scale_bar_count_is_chunked_at_runtime():
    """Start validation no longer rejects on bar estimate; OHLC loads in windows."""
    assert simulation_start_validation_error(date(2024, 1, 1), date(2024, 12, 31), scale="1m") is None


def test_validation_accepts_reasonable_daily():
    assert simulation_start_validation_error(date(2024, 1, 1), date(2024, 12, 31), scale="1d") is None


def test_validation_accepts_long_calendar_span():
    assert simulation_start_validation_error(date(2010, 1, 1), date(2026, 1, 1), scale="1d") is None


def test_read_strategy_scale_reads_params(tmp_path: Path):
    p = tmp_path / "params.json"
    p.write_text('{"scale": "4H", "ticker": "X"}', encoding="utf-8")
    assert read_strategy_scale(p) == "4h"


def test_plan_display_chunks_minute_year_stays_under_cap_per_chunk():
    start = date(2024, 1, 1)
    end = date(2024, 12, 31)
    chunks = plan_display_bars_fetch_chunks(start, end, "1m")
    assert len(chunks) >= 4
    assert chunks[0][0] == start
    assert chunks[-1][1] == end
    for s, e in chunks:
        assert s <= e
        assert estimate_source_bar_count(s, e, "1m") <= CHUNK_BAR_BUDGET
    for i in range(len(chunks) - 1):
        assert chunks[i + 1][0] == chunks[i][1] + timedelta(days=1)


def test_plan_display_single_chunk_when_under_cap():
    start = date(2024, 1, 1)
    end = date(2024, 1, 31)
    chunks = plan_display_bars_fetch_chunks(start, end, "1m")
    assert chunks == [(start, end)]


def test_plan_display_empty_when_span_invalid():
    assert plan_display_bars_fetch_chunks(date(2024, 2, 1), date(2024, 1, 1), "1m") == []
