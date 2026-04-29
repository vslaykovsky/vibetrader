from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

try:
    import dotenv

    dotenv.load_dotenv(_BACKEND / ".env")
except Exception:
    pass

from db.session import engine, init_database


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Create tables and apply legacy strategy column updates.")
    p.parse_args(argv)
    init_database(engine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
