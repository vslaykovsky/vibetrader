from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run backend.services.agent._run_codex_exec")
    parser.add_argument("task", help="Task/prompt string passed to `codex exec`.")
    parser.add_argument(
        "--cwd",
        default=".",
        help="Working directory to run codex in (default: current directory).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))

    from backend.services.agent import _run_codex_exec

    proc = _run_codex_exec(args.task, Path(args.cwd).resolve())
    if proc.stdout:
        sys.stdout.write(proc.stdout)
        if not proc.stdout.endswith("\n"):
            sys.stdout.write("\n")
    if proc.stderr:
        sys.stderr.write(proc.stderr)
        if not proc.stderr.endswith("\n"):
            sys.stderr.write("\n")
    return int(proc.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

