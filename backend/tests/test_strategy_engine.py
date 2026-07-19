import pytest

from application.services.strategy_engine import (
    StrategyEngine,
    StrategyTransport,
    configured_strategy_engine,
    configured_strategy_transport,
    detect_strategy_source_engine,
    strategy_entrypoint,
)


def test_strategy_engine_defaults_to_python(monkeypatch):
    monkeypatch.delenv("STRATEGY_ENGINE", raising=False)
    monkeypatch.delenv("STRATEGY_IPC_TRANSPORT", raising=False)
    assert configured_strategy_engine() is StrategyEngine.PYTHON
    assert strategy_entrypoint() == "strategy.py"
    assert configured_strategy_transport() is StrategyTransport.JSONL


def test_rust_engine_defaults_to_jsonl(monkeypatch):
    monkeypatch.setenv("STRATEGY_ENGINE", "rust")
    monkeypatch.delenv("STRATEGY_IPC_TRANSPORT", raising=False)
    assert strategy_entrypoint() == "strategy.rs"
    assert configured_strategy_transport() is StrategyTransport.JSONL


def test_python_rejects_msgpack_transport(monkeypatch):
    monkeypatch.setenv("STRATEGY_ENGINE", "python")
    monkeypatch.setenv("STRATEGY_IPC_TRANSPORT", "msgpack")
    with pytest.raises(ValueError, match="Python strategies support only"):
        configured_strategy_transport()


def test_invalid_engine_is_rejected(monkeypatch):
    monkeypatch.setenv("STRATEGY_ENGINE", "go")
    with pytest.raises(ValueError, match="STRATEGY_ENGINE must be one of"):
        configured_strategy_engine()


def test_detect_strategy_source_engine_identifies_python():
    source = """from __future__ import annotations
import json

def emit_order(price: float) -> str:
    return f"buy at {price:.2f}"
"""
    assert detect_strategy_source_engine(source) is StrategyEngine.PYTHON


def test_detect_strategy_source_engine_identifies_rust():
    source = """use crate::utils::StrategyOutput;

pub struct StrategyState {
    position: i64,
}

pub fn process(state: &mut StrategyState) -> StrategyOutput {
    let mut output = StrategyOutput::default();
    output
}
"""
    assert detect_strategy_source_engine(source) is StrategyEngine.RUST


def test_detect_strategy_source_engine_leaves_ambiguous_source_unknown():
    assert detect_strategy_source_engine("value = 1;") is None
    assert detect_strategy_source_engine("# only a comment") is None
