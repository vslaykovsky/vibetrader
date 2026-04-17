import argparse
from utils import *


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    params = load_params()
    name = str(params.get("strategy_name") or "Untitled")
    save_backtest_json(DataJson(strategy_name=name, charts=[]))


if __name__ == "__main__":
    main()
