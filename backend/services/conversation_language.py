from __future__ import annotations

import threading
from typing import Any

from lingua import LanguageDetectorBuilder

_detector: Any = None
_detector_lock = threading.Lock()


def _get_detector():
    global _detector
    if _detector is None:
        with _detector_lock:
            if _detector is None:
                _detector = LanguageDetectorBuilder.from_all_languages().build()
    return _detector


def _message_text(m: dict[str, Any]) -> str:
    role = str(m.get("role", ""))
    if role not in ("user", "assistant"):
        return ""
    raw = m.get("content", "")
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return str(raw)


def detect_conversation_language_iso(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for m in messages:
        if isinstance(m, dict):
            parts.append(_message_text(m))
    tail = "".join(parts)[-1000:]
    if not tail.strip():
        return ""
    lang = _get_detector().detect_language_of(tail)
    if lang is None:
        return ""
    iso = lang.iso_code_639_1
    return iso.name.lower() if iso is not None else ""
