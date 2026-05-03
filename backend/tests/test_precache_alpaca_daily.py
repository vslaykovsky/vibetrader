import logging

from scripts import precache_alpaca_daily as precache


def test_precache_accepts_moex_provider_with_symbols_file(tmp_path, caplog):
    symbols_file = tmp_path / "moex.txt"
    symbols_file.write_text("sber\n# ignored\ngazp\n", encoding="utf-8")
    caplog.set_level(logging.INFO, logger=precache.__name__)

    out = precache.main(
        [
            "--provider",
            "moex",
            "--symbols-file",
            str(symbols_file),
            "--dry-run",
        ]
    )

    assert out == 0
    assert "provider=moex" in caplog.text
    assert "dry-run symbol=GAZP" in caplog.text
    assert "dry-run symbol=SBER" in caplog.text
