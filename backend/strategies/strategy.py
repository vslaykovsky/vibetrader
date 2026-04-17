import argparse
from utils import *


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Placeholder strategy workspace. Implement data loading and runs."
    )
    parser.parse_args()
    ensure_output_dir()
    params = load_params()
    name = str(params.get("strategy_name") or "Untitled")
    save_data_json(DataJson(strategy_name=name, charts=[], table=[]))


if __name__ == "__main__":
    main()
