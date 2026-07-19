from __future__ import annotations

import json
import subprocess

import pytest

import services.agent as agent


PYTHON_STRATEGY = """from __future__ import annotations
import json

def process(price: float) -> str:
    return f"buy at {price:.2f}"
"""

RUST_STRATEGY = """use crate::utils::StrategyOutput;

pub struct StrategyState {
    position: i64,
}

pub fn process(state: &mut StrategyState) -> StrategyOutput {
    let mut output = StrategyOutput::default();
    output
}
"""


def _codex_result(thread_id: str = "migration-thread") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["codex", "exec"],
        returncode=0,
        stdout=json.dumps({"type": "thread.started", "thread_id": thread_id}) + "\n",
        stderr="",
    )


def test_python_strategy_is_automatically_converted_to_rust(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEGY_ENGINE", "rust")
    monkeypatch.setattr(agent, "ensure_strategy_workspace", lambda _thread_id: tmp_path)
    (tmp_path / "strategy.rs").write_text(PYTHON_STRATEGY, encoding="utf-8")
    (tmp_path / "params.json").write_text('{"ema_period": 9}', encoding="utf-8")
    prompts: list[str] = []

    def fake_coding_agent(task, cwd, codex_thread_id=None, cancel_control=None):
        prompts.append(task)
        assert cwd == tmp_path
        assert codex_thread_id is None
        (tmp_path / "strategy.rs").write_text(RUST_STRATEGY, encoding="utf-8")
        (tmp_path / "params.json").write_text('{"ema_period": 999}', encoding="utf-8")
        return "codex", _codex_result()

    monkeypatch.setattr(agent, "_run_coding_agent_exec", fake_coding_agent)
    monkeypatch.setattr(agent, "_strategy_language_validation_error", lambda *_args: "")

    result = agent.ensure_strategy_source_language("thread-id")

    assert result == {
        "ok": True,
        "migrated": True,
        "source_engine": "python",
        "target_engine": "rust",
        "codex_thread_id": "migration-thread",
    }
    assert "automatic source-language migration, not a strategy change" in prompts[0]
    assert "Preserve trading behavior" in prompts[0]
    assert "Preserve every params.json key and current value" in prompts[0]
    assert (tmp_path / "params.json").read_text(encoding="utf-8") == '{"ema_period": 9}'


def test_rust_strategy_is_automatically_converted_to_python(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEGY_ENGINE", "python")
    monkeypatch.setattr(agent, "ensure_strategy_workspace", lambda _thread_id: tmp_path)
    (tmp_path / "strategy.py").write_text(RUST_STRATEGY, encoding="utf-8")

    def fake_coding_agent(task, cwd, codex_thread_id=None, cancel_control=None):
        assert "translate the entire implementation in place to python" in task
        (tmp_path / "strategy.py").write_text(PYTHON_STRATEGY, encoding="utf-8")
        return "codex", _codex_result("python-migration-thread")

    monkeypatch.setattr(agent, "_run_coding_agent_exec", fake_coding_agent)
    monkeypatch.setattr(agent, "_strategy_language_validation_error", lambda *_args: "")

    result = agent.ensure_strategy_source_language("thread-id")

    assert result["migrated"] is True
    assert result["source_engine"] == "rust"
    assert result["target_engine"] == "python"
    assert result["codex_thread_id"] == "python-migration-thread"


def test_correct_strategy_language_does_not_run_coding_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEGY_ENGINE", "rust")
    monkeypatch.setattr(agent, "ensure_strategy_workspace", lambda _thread_id: tmp_path)
    (tmp_path / "strategy.rs").write_text(RUST_STRATEGY, encoding="utf-8")

    def fail_coding_agent(*_args, **_kwargs):
        raise AssertionError("coding agent should not run")

    monkeypatch.setattr(agent, "_run_coding_agent_exec", fail_coding_agent)

    result = agent.ensure_strategy_source_language("thread-id")

    assert result["migrated"] is False
    assert result["source_engine"] == "rust"


def test_conversion_gets_one_compiler_guided_repair(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEGY_ENGINE", "rust")
    monkeypatch.setattr(agent, "ensure_strategy_workspace", lambda _thread_id: tmp_path)
    (tmp_path / "strategy.rs").write_text(PYTHON_STRATEGY, encoding="utf-8")
    prompts: list[str] = []

    def fake_coding_agent(task, cwd, codex_thread_id=None, cancel_control=None):
        prompts.append(task)
        (tmp_path / "strategy.rs").write_text(RUST_STRATEGY, encoding="utf-8")
        if len(prompts) == 1:
            assert codex_thread_id is None
            return "codex", _codex_result("migration-thread")
        assert codex_thread_id == "migration-thread"
        return "codex", _codex_result("migration-thread")

    diagnostics = iter(["error[E0308]: mismatched types", ""])
    monkeypatch.setattr(agent, "_run_coding_agent_exec", fake_coding_agent)
    monkeypatch.setattr(
        agent,
        "_strategy_language_validation_error",
        lambda *_args: next(diagnostics),
    )

    result = agent.ensure_strategy_source_language("thread-id")

    assert result["migrated"] is True
    assert len(prompts) == 2
    assert "error[E0308]: mismatched types" in prompts[1]
    assert "Fix only translation or compilation issues" in prompts[1]


def test_failed_conversion_restores_source_and_parameters(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEGY_ENGINE", "rust")
    monkeypatch.setattr(agent, "ensure_strategy_workspace", lambda _thread_id: tmp_path)
    strategy_path = tmp_path / "strategy.rs"
    params_path = tmp_path / "params.json"
    strategy_path.write_text(PYTHON_STRATEGY, encoding="utf-8")
    params_path.write_text('{"ema_period": 9}', encoding="utf-8")

    def fake_coding_agent(task, cwd, codex_thread_id=None, cancel_control=None):
        strategy_path.write_text("incomplete rust", encoding="utf-8")
        params_path.write_text('{"ema_period": 999}', encoding="utf-8")
        return "codex", subprocess.CompletedProcess(
            args=["codex", "exec"],
            returncode=1,
            stdout="",
            stderr="conversion failed",
        )

    monkeypatch.setattr(agent, "_run_coding_agent_exec", fake_coding_agent)

    with pytest.raises(agent.StrategyLanguageConversionError, match="conversion failed"):
        agent.ensure_strategy_source_language("thread-id")

    assert strategy_path.read_text(encoding="utf-8") == PYTHON_STRATEGY
    assert params_path.read_text(encoding="utf-8") == '{"ema_period": 9}'


def test_build_agent_reply_uses_migration_codex_thread(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(
        agent,
        "ensure_strategy_source_language",
        lambda *_args, **_kwargs: {
            "ok": True,
            "migrated": True,
            "codex_thread_id": "migration-thread",
        },
    )
    monkeypatch.setattr(agent, "canvas_with_output", lambda canvas, _thread_id: canvas)

    result = agent.build_agent_reply(
        messages=[],
        existing_canvas={},
        thread_id="thread-id",
        codex_thread_id="old-python-thread",
    )

    assert result["codex_thread_id"] == "migration-thread"
