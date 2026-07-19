from __future__ import annotations

import ast
import os
import re
from enum import StrEnum


class StrategyEngine(StrEnum):
    PYTHON = "python"
    RUST = "rust"


class StrategyTransport(StrEnum):
    JSONL = "jsonl"
    MSGPACK = "msgpack"


def configured_strategy_engine() -> StrategyEngine:
    raw = (os.getenv("STRATEGY_ENGINE") or StrategyEngine.PYTHON.value).strip().lower()
    try:
        return StrategyEngine(raw)
    except ValueError as exc:
        choices = ", ".join(engine.value for engine in StrategyEngine)
        raise ValueError(f"STRATEGY_ENGINE must be one of: {choices}; got {raw!r}") from exc


def strategy_entrypoint(engine: StrategyEngine | None = None) -> str:
    selected = engine or configured_strategy_engine()
    return "strategy.rs" if selected is StrategyEngine.RUST else "strategy.py"


_RUST_SOURCE_PATTERNS = (
    re.compile(r"(?m)^\s*(?:pub\s+)?(?:async\s+)?fn\s+[A-Za-z_]\w*\s*\("),
    re.compile(r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|impl|mod)\b"),
    re.compile(r"(?m)^\s*use\s+(?:crate|self|super|std)::"),
    re.compile(r"(?m)^\s*let\s+(?:mut\s+)?[A-Za-z_]\w*\s*(?::|=)"),
)

_PYTHON_SOURCE_PATTERNS = (
    re.compile(r"(?m)^\s*(?:async\s+)?def\s+[A-Za-z_]\w*\s*\("),
    re.compile(r"(?m)^\s*class\s+[A-Za-z_]\w*\s*(?:\(|:)"),
    re.compile(r"(?m)^\s*from\s+[A-Za-z_.]+\s+import\s+"),
    re.compile(r"(?m)^\s*import\s+[A-Za-z_.]+"),
)


def detect_strategy_source_engine(source: str | None) -> StrategyEngine | None:
    """Return a language only when the strategy source is unambiguous."""
    text = (source or "").strip()
    if not text:
        return None

    rust_score = sum(bool(pattern.search(text)) for pattern in _RUST_SOURCE_PATTERNS)
    python_score = sum(bool(pattern.search(text)) for pattern in _PYTHON_SOURCE_PATTERNS)
    try:
        parsed = ast.parse(text)
    except SyntaxError:
        parsed = None
    if parsed is not None and parsed.body and python_score >= 1:
        return StrategyEngine.PYTHON

    if rust_score >= 2 and rust_score > python_score:
        return StrategyEngine.RUST
    if python_score >= 2 and python_score > rust_score:
        return StrategyEngine.PYTHON
    return None


def configured_strategy_transport(
    engine: StrategyEngine | None = None,
) -> StrategyTransport:
    selected = engine or configured_strategy_engine()
    default = StrategyTransport.JSONL
    raw = (os.getenv("STRATEGY_IPC_TRANSPORT") or default.value).strip().lower()
    try:
        transport = StrategyTransport(raw)
    except ValueError as exc:
        choices = ", ".join(item.value for item in StrategyTransport)
        raise ValueError(
            f"STRATEGY_IPC_TRANSPORT must be one of: {choices}; got {raw!r}"
        ) from exc
    if selected is StrategyEngine.PYTHON and transport is not StrategyTransport.JSONL:
        raise ValueError(
            "Python strategies support only STRATEGY_IPC_TRANSPORT=jsonl"
        )
    return transport


def engine_for_entrypoint(entrypoint: str) -> StrategyEngine:
    return StrategyEngine.RUST if entrypoint.lower().endswith(".rs") else StrategyEngine.PYTHON
