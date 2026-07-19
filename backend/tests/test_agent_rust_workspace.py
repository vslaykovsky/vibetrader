from pathlib import Path

import services.agent as agent


def test_rust_workspace_uses_rust_templates(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEGY_ENGINE", "rust")
    monkeypatch.setattr(agent, "STRATEGIES_DIR", tmp_path)

    workspace = agent.ensure_strategy_workspace("11111111-1111-1111-1111-111111111111")

    assert workspace == tmp_path / "11111111-1111-1111-1111-111111111111"
    assert (workspace / "strategy.rs").is_file()
    assert (workspace / "utils.rs").is_file()
    assert (workspace / "worker.rs").is_file()
    assert (workspace / "simulator.rs").is_file()
    assert (workspace / "optimizer.rs").is_file()
    assert (workspace / "optimizer_runtime.rs").is_file()
    assert (workspace / "portfolio.rs").is_file()
    assert (workspace / "utils.py").is_file()
    assert (workspace / "hyperopt.py").is_file()
    assert "Rust strategy workspace" in (workspace / "AGENTS.md").read_text()
    manifest = (workspace / "Cargo.toml").read_text()
    lockfile = (workspace / "Cargo.lock").read_text()
    assert "{{CRATE_NAME}}" not in manifest
    assert "{{CRATE_NAME}}" not in lockfile
    assert "vibetrader_strategy_" in manifest
    assert "[workspace]" in manifest
    manifest_mtime = (workspace / "Cargo.toml").stat().st_mtime_ns
    agent.ensure_strategy_workspace("11111111-1111-1111-1111-111111111111")
    assert (workspace / "Cargo.toml").stat().st_mtime_ns == manifest_mtime


def test_rust_workspace_read_and_restore_use_strategy_rs(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEGY_ENGINE", "rust")
    monkeypatch.setattr(agent, "STRATEGIES_DIR", tmp_path)
    thread_id = "22222222-2222-2222-2222-222222222222"

    agent.restore_strategy_workspace_from_snapshot(
        thread_id,
        code="fn main() {}\n",
        canvas={
            "output": {
                "params.json": {"ticker": "SPY"},
                "params-hyperopt.json": {
                    "search_space": {},
                    "mode": "walk_forward",
                    "walk_forward": {
                        "train_window_days": 30,
                        "test_window_days": 5,
                        "step_days": 5,
                        "oos_total_days": 60,
                    },
                },
            }
        },
    )

    assert agent.read_strategy_code(thread_id) == "fn main() {}\n"
    assert not (Path(tmp_path) / thread_id / "strategy.py").is_file()
    saved_hyperopt = (Path(tmp_path) / thread_id / "params-hyperopt.json").read_text()
    assert '"step_days"' not in saved_hyperopt
