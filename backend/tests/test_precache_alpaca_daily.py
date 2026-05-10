import logging

from scripts import precache_alpaca_daily as precache


def test_precache_accepts_moex_provider_with_symbols_inputs(tmp_path, caplog):
    symbols_file = tmp_path / "moex.txt"
    symbols_file.write_text("sber\n# ignored\ngazp\n", encoding="utf-8")
    caplog.set_level(logging.INFO, logger=precache.__name__)

    out = precache.main(
        [
            "--provider",
            "moex",
            "--months",
            "1",
            "--end",
            "2026-03-31",
            "--symbols-file",
            str(symbols_file),
            "--symbols",
            "lkoh",
            "gazp",
            "--session",
            "extended",
            "--dry-run",
        ]
    )

    assert out == 0
    assert "provider=moex" in caplog.text
    assert "session=extended" in caplog.text
    assert "window=2026-02-28..2026-03-31" in caplog.text
    assert "dry-run symbol=GAZP" in caplog.text
    assert "dry-run symbol=LKOH" in caplog.text
    assert "dry-run symbol=SBER" in caplog.text
