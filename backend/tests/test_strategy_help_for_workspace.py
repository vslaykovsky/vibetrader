from pathlib import Path

from services.agent import _strategy_help_for_workspace


def test_strategy_help_for_workspace_reflects_current_params_json(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "params.json").write_text('{"ticker": "AAA"}', encoding="utf-8")
    first = _strategy_help_for_workspace(ws)
    assert "AAA" in first
    (ws / "params.json").write_text('{"ticker": "BBB"}', encoding="utf-8")
    second = _strategy_help_for_workspace(ws)
    assert "BBB" in second
