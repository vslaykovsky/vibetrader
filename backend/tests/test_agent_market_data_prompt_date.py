import json
from datetime import datetime, timezone

from application.services.market_calendar import latest_xnys_session_on_or_before
from services import agent


def _write_params(tmp_path, *, ticker: str, provider: str = "alpaca") -> None:
    (tmp_path / "params.json").write_text(
        json.dumps({"ticker": ticker, "provider": provider}),
        encoding="utf-8",
    )


def test_latest_xnys_session_skips_weekends_and_holidays():
    assert latest_xnys_session_on_or_before(
        datetime(2026, 7, 4, tzinfo=timezone.utc).date()
    ).isoformat() == "2026-07-02"


def test_system_prompt_uses_request_time_and_xnys_session(monkeypatch, tmp_path):
    _write_params(tmp_path, ticker="ORCL")
    monkeypatch.setattr(agent, "_strategy_help_for_workspace", lambda _workspace: "help")

    prompt = agent._system_prompt_for_workspace(
        tmp_path,
        now_utc=datetime(2026, 7, 5, 12, tzinfo=timezone.utc),
    )

    assert "Current UTC date is 2026-07-05" in prompt
    assert "provider data cap is 2026-07-04 inclusive" in prompt
    assert "latest permitted market-data date is 2026-07-02" in prompt
    assert "latest XNYS trading session" in prompt


def test_system_prompt_date_is_not_frozen_at_import(monkeypatch, tmp_path):
    _write_params(tmp_path, ticker="ORCL")
    monkeypatch.setattr(agent, "_strategy_help_for_workspace", lambda _workspace: "help")

    first = agent._system_prompt_for_workspace(
        tmp_path,
        now_utc=datetime(2026, 8, 22, 1, tzinfo=timezone.utc),
    )
    second = agent._system_prompt_for_workspace(
        tmp_path,
        now_utc=datetime(2026, 8, 25, 1, tzinfo=timezone.utc),
    )

    assert "latest permitted market-data date is 2026-08-21" in first
    assert "latest permitted market-data date is 2026-08-24" in second


def test_crypto_prompt_uses_calendar_day_provider_cap(monkeypatch, tmp_path):
    _write_params(tmp_path, ticker="BTC/USD")
    monkeypatch.setattr(agent, "_strategy_help_for_workspace", lambda _workspace: "help")

    prompt = agent._system_prompt_for_workspace(
        tmp_path,
        now_utc=datetime(2026, 8, 23, 1, tzinfo=timezone.utc),
    )

    assert "provider data cap is 2026-08-22 inclusive" in prompt
    assert "latest permitted market-data date is 2026-08-22" in prompt
    assert "one-UTC-calendar-day data delay" in prompt
