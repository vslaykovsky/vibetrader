from __future__ import annotations

from services.supabase_trading_settings import mask_secret_tail


def test_mask_secret_tail():
    assert mask_secret_tail("") == ""
    assert mask_secret_tail("ab") == "****"
    assert mask_secret_tail("abcde") == "****bcde"
    assert mask_secret_tail("abcdefghij", tail=4) == "****ghij"
