from services.agent import _parse_argparse_help_description


def test_parse_argparse_help_description():
    spy_help = """usage: strategy.py [-h] [--backtest]

Long-only daily SMA crossover strategy for SPY that buys when the 50-day SMA crosses above the 200-day SMA and sells when the
50-day SMA crosses below it, with no stop loss or take profit.

options:
  -h, --help  show this help message and exit
  --backtest  Run the backtest using output/params.json.
"""
    assert _parse_argparse_help_description(spy_help) == (
        "Long-only daily SMA crossover strategy for SPY that buys when the 50-day SMA crosses above the 200-day SMA and sells when the\n"
        "50-day SMA crosses below it, with no stop loss or take profit."
    )
    assert (
        _parse_argparse_help_description(
            "usage: x [-h]\n\nFirst line.\n\nSecond paragraph.\n\noptional arguments:\n  -h\n"
        )
        == "First line.\n\nSecond paragraph."
    )
    assert _parse_argparse_help_description("usage: x\n\n\npositional arguments:\n  path\n") == ""
    assert _parse_argparse_help_description("") == ""
    assert _parse_argparse_help_description("usage: x\n\n") == ""
