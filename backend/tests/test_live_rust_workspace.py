from types import SimpleNamespace
import uuid

from scripts.run_alpaca_strategy import _materialize_workspace_from_db


def test_live_workspace_materializes_rust_worker(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEGY_ENGINE", "rust")
    row = SimpleNamespace(
        id=uuid.uuid4(),
        code=(
            "use crate::utils::*;\n"
            "pub struct GeneratedStrategy;\n"
            "impl StrategyHandler for GeneratedStrategy {\n"
            "fn startup(&self) -> Vec<OutputDataPoint> { vec![] }\n"
            "fn on_step(&mut self, _: &StrategyInput) -> Vec<OutputDataPoint> { vec![] }\n"
            "}\n"
        ),
        canvas={"output": {"params.json": {"ticker": "SPY"}}},
    )

    entry_name = _materialize_workspace_from_db(row, tmp_path)

    assert entry_name == "strategy.rs"
    assert (tmp_path / "strategy.rs").read_text(encoding="utf-8") == row.code
    for name in (
        "utils.rs",
        "worker.rs",
        "simulator.rs",
        "optimizer.rs",
        "optimizer_runtime.rs",
        "portfolio.rs",
        "Cargo.toml",
        "Cargo.lock",
    ):
        assert (tmp_path / name).is_file()
    assert "{{CRATE_NAME}}" not in (tmp_path / "Cargo.toml").read_text(
        encoding="utf-8"
    )
