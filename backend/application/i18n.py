from __future__ import annotations

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "sim.call_init_first": "call POST /simulation/init before play",
        "sim.no_ohlc": (
            "Provider returned no OHLC for the requested range — "
            "try an earlier start date or a different ticker."
        ),
        "sim.no_market_bars": (
            "No market bars available at or after the chosen "
            "start date — try an earlier start."
        ),
        "sim.no_base_scale_bars": "No base-scale bars after aggregation.",
        "err.invalid_thread_id": "invalid or missing thread_id",
        "err.start_date_required": "start_date is required (YYYY-MM-DD)",
        "err.deposit_not_number": "initial_deposit must be a number",
        "err.deposit_not_positive": "initial_deposit must be positive",
        "err.start_end_required": "start_date and end_date are required (YYYY-MM-DD)",
        "err.start_before_end": "start_date must be on or before end_date",
        "err.no_active_simulation": "no active simulation for this thread",
        "err.unsupported_initial_scale": "unsupported initial_scale",
        "err.unsupported_scale": "unsupported scale",
    },
    "ru": {
        "sim.call_init_first": "Сначала выполните POST /simulation/init",
        "sim.no_ohlc": (
            "Провайдер не вернул данные OHLC для указанного диапазона — "
            "попробуйте более раннюю дату начала или другой тикер."
        ),
        "sim.no_market_bars": (
            "Нет рыночных баров для выбранной даты начала — "
            "попробуйте более раннюю дату."
        ),
        "sim.no_base_scale_bars": "Нет баров базового масштаба после агрегации.",
        "err.invalid_thread_id": "неверный или отсутствующий thread_id",
        "err.start_date_required": "start_date обязателен (ГГГГ-ММ-ДД)",
        "err.deposit_not_number": "initial_deposit должен быть числом",
        "err.deposit_not_positive": "initial_deposit должен быть положительным",
        "err.start_end_required": "start_date и end_date обязательны (ГГГГ-ММ-ДД)",
        "err.start_before_end": "start_date должна быть не позже end_date",
        "err.no_active_simulation": "нет активной симуляции для этого потока",
        "err.unsupported_initial_scale": "неподдерживаемый initial_scale",
        "err.unsupported_scale": "неподдерживаемый scale",
    },
}


def detect_lang(accept_language: str | None) -> str:
    if accept_language:
        primary = accept_language.split(",")[0].split(";")[0].strip().lower()
        if primary.startswith("ru"):
            return "ru"
    return "en"


def t(key: str, lang: str = "en") -> str:
    lang = lang if lang in _TRANSLATIONS else "en"
    return _TRANSLATIONS[lang].get(key) or _TRANSLATIONS["en"].get(key) or key
