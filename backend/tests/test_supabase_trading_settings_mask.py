from __future__ import annotations

from services.supabase_trading_settings import (
    mask_secret_tail,
    normalize_hour_format,
    normalize_message_limit_5h,
)


def test_mask_secret_tail():
    assert mask_secret_tail("") == ""
    assert mask_secret_tail("ab") == "****"
    assert mask_secret_tail("abcde") == "****bcde"
    assert mask_secret_tail("abcdefghij", tail=4) == "****ghij"


def test_normalize_hour_format():
    assert normalize_hour_format("auto") == "auto"
    assert normalize_hour_format("12H") == "12h"
    assert normalize_hour_format("24h") == "24h"
    assert normalize_hour_format("browser") == ""


def test_normalize_message_limit_5h():
    assert normalize_message_limit_5h(12) == 12
    assert normalize_message_limit_5h("8") == 8
    assert normalize_message_limit_5h(0) == 5
    assert normalize_message_limit_5h(True) == 5
