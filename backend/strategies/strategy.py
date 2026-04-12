import argparse
from utils import *


def run_backtest() -> None:
    # TODO backtest code here
    pass


def run_eda() -> None:
    # TODO eda code here
    pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description=""
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--backtest",
        action="store_true",
        help="Run the backtest using output/params.json",
    )
    mode.add_argument(
        "--eda",
        action="store_true",
        help="Run exploratory data analysis using output/params.json (not a strategy backtest)",
    )
    args = parser.parse_args()
    if args.backtest:
        run_backtest()
        return
    if args.eda:
        run_eda()
        return
    parser.print_help()


if __name__ == "__main__":
    main()
