from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

from sqlalchemy import and_

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

try:
    import dotenv

    dotenv.load_dotenv(_BACKEND_ROOT / ".env")
except Exception:
    pass

from application.services.live_run_control import live_run_row_requests_stop
from db.models import LiveRun
from db.session import SessionLocal
from services.supabase_trading_settings import fetch_profile_alpaca_keys, service_role_configured


def _load_run(run_id: str) -> LiveRun:
    session = SessionLocal()
    try:
        row = session.get(LiveRun, run_id)
        if row is None:
            raise SystemExit(f"live_runs has no row for run_id={run_id!r}")
        return row
    finally:
        session.close()


def _runner_env(row: LiveRun) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    uid = (row.created_by or "").strip()
    if service_role_configured() and uid:
        pair = fetch_profile_alpaca_keys(uid)
        if pair:
            env["ALPACA_API_KEY"] = pair[0]
            env["ALPACA_SECRET_KEY"] = pair[1]
    return env


def _build_cmd(row: LiveRun, *, enable_trading: bool) -> list[str]:
    entry = Path(row.entry_path)
    if not entry.is_file():
        raise SystemExit(f"entry_path is not a file: {entry}")
    rid = (row.id or "").strip()
    script = _BACKEND_ROOT / "scripts" / "run_alpaca_strategy.py"
    cmd: list[str] = [
        sys.executable,
        str(script),
        "--entry",
        str(entry),
        "--run-id",
        rid,
        "--runner-id",
        (row.runner_id or rid)[:64],
    ]
    cb = (row.created_by or "").strip()
    if cb:
        cmd += ["--created-by", cb]
    cbe = (row.created_by_email or "").strip()
    if cbe:
        cmd += ["--created-by-email", cbe]
    if (row.mode or "").strip().lower() == "paper":
        cmd.append("--paper")
    if enable_trading:
        cmd.append("--enable-trading")
    return cmd


def _query_local_starting(session) -> list[LiveRun]:
    return (
        session.query(LiveRun)
        .filter(
            and_(
                LiveRun.runner_backend == "local",
                LiveRun.status == "starting",
            )
        )
        .order_by(LiveRun.created_at.asc())
        .all()
    )


def _run_explicit(args: argparse.Namespace) -> int:
    rows = [_load_run(rid.strip()) for rid in args.run_ids if rid.strip()]
    cmds = [_build_cmd(r, enable_trading=bool(args.enable_trading)) for r in rows]
    if args.dry_run:
        for c in cmds:
            logger.info("%s", " ".join(c))
        return 0
    procs: list[subprocess.Popen] = []
    for i, c in enumerate(cmds):
        rid = (rows[i].id or "").strip()
        logger.info("spawn run_id=%s cmd=%s", rid, " ".join(c))
        procs.append(
            subprocess.Popen(
                c,
                cwd=str(_BACKEND_ROOT),
                env=_runner_env(rows[i]),
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
        )
    rc = 0
    try:
        while procs:
            time.sleep(0.5)
            for i in range(len(procs) - 1, -1, -1):
                pobj = procs[i]
                r = pobj.poll()
                if r is not None:
                    procs.pop(i)
                    if r != 0:
                        rc = r
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt, terminating %s worker(s)", len(procs))
        for pobj in procs:
            try:
                pobj.terminate()
            except Exception:
                pass
        raise SystemExit(130) from None
    return rc


def _run_watch(args: argparse.Namespace) -> int:
    interval = max(0.5, float(args.watch_interval_s))
    spawned: dict[str, subprocess.Popen] = {}
    rc = 0
    logger.info(
        "watch mode interval_s=%s enable_trading=%s dry_run=%s",
        interval,
        bool(args.enable_trading),
        bool(args.dry_run),
    )
    try:
        while True:
            session = SessionLocal()
            try:
                for rid, proc in list(spawned.items()):
                    row = session.get(LiveRun, rid)
                    if live_run_row_requests_stop(row) and proc.poll() is None:
                        proc.terminate()
                for rid in list(spawned.keys()):
                    proc = spawned.get(rid)
                    if proc is not None and proc.poll() is not None:
                        del spawned[rid]
                        if proc.returncode not in (0, None):
                            rc = proc.returncode or rc
                rows = _query_local_starting(session)
            finally:
                session.close()
            for row in rows:
                rid = (row.id or "").strip()
                if not rid or rid in spawned:
                    continue
                proc = spawned.get(rid)
                if proc is not None and proc.poll() is None:
                    continue
                cmd = _build_cmd(row, enable_trading=bool(args.enable_trading))
                if args.dry_run:
                    logger.info("%s", " ".join(cmd))
                    continue
                logger.info("spawn run_id=%s cmd=%s", rid, " ".join(cmd))
                spawned[rid] = subprocess.Popen(
                    cmd,
                    cwd=str(_BACKEND_ROOT),
                    env=_runner_env(row),
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                )
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt, terminating %s worker(s)", len(spawned))
        for proc in spawned.values():
            try:
                proc.terminate()
            except Exception:
                pass
        raise SystemExit(130) from None


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        stream=sys.stderr,
    )
    p = argparse.ArgumentParser(
        description="Default: poll live_runs for local starting runs and spawn workers. With run_ids, start those runs once and exit when they exit."
    )
    p.add_argument(
        "run_ids",
        nargs="*",
        default=[],
        help="Optional: live run UUIDs to start once. If omitted, watch the DB (runner_backend=local, status=starting).",
    )
    p.add_argument(
        "--watch-interval-s",
        type=float,
        default=2.0,
        help="Seconds between DB polls in watch mode (no run_ids).",
    )
    p.add_argument(
        "--enable-trading",
        action="store_true",
        help="Forward --enable-trading to each spawned worker.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands instead of executing.",
    )
    args = p.parse_args(argv)
    run_ids = [x.strip() for x in args.run_ids if x.strip()]
    if run_ids:
        args.run_ids = run_ids
        logger.info(
            "explicit mode run_ids=%s enable_trading=%s dry_run=%s",
            run_ids,
            bool(args.enable_trading),
            bool(args.dry_run),
        )
        return _run_explicit(args)
    return _run_watch(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
