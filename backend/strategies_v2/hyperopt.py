from __future__ import annotations

import json
import logging
import math
import os
import random
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

from pydantic import ValidationError

from utils import (
    HyperoptCategoricalSpec,
    HyperoptFloatSpec,
    HyperoptIntSpec,
    ParamsHyperopt,
)

WORKSPACE = Path(__file__).resolve().parent
PARAMS_PATH = WORKSPACE / "params.json"
PARAMS_HYPEROPT_PATH = WORKSPACE / "params-hyperopt.json"
METRICS_PATH = WORKSPACE / "metrics.json"


def _locate_simulate_script(start: Path) -> Path:
    for parent in [start, *start.parents]:
        candidate = parent / "scripts" / "simulate_strategy_v2.py"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Could not locate scripts/simulate_strategy_v2.py above the workspace")


SIMULATE_SCRIPT = _locate_simulate_script(WORKSPACE)

logger = logging.getLogger(__name__)


class _HyperoptJsonlFormatter(logging.Formatter):
    _SEVERITY = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "severity": self._SEVERITY.get(record.levelno, "DEFAULT"),
            "logger": record.name,
            "message": record.getMessage(),
        }
        return json.dumps(payload, default=str)


def configure_logging(log_level: str | None = None) -> None:
    lvl_name = (log_level or os.environ.get("HYPEROPT_LOG_LEVEL", "INFO")).upper()
    numeric = getattr(logging, lvl_name, logging.INFO)
    root = logging.getLogger()
    root.handlers.clear()
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(_HyperoptJsonlFormatter())
    root.addHandler(h)
    root.setLevel(numeric)
    logger.setLevel(numeric)


def _emit_ui(payload: dict) -> None:
    line = json.dumps({"hyperopt_ui": True, **payload}, default=str)
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def _best_value_for_ui(best_params: dict | None, best_value: float) -> float | None:
    if best_params is None:
        return None
    if isinstance(best_value, float) and (math.isinf(best_value) or math.isnan(best_value)):
        return None
    return float(best_value)


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _save_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _nested_get(obj: dict, dotted: str):
    cur: object = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _load_params_hyperopt(path: Path) -> ParamsHyperopt | None:
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    return ParamsHyperopt.model_validate_json(raw)


def _sample_from_space(rng: random.Random, space: dict) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, spec in space.items():
        if isinstance(spec, HyperoptIntSpec):
            out[key] = rng.randint(int(spec.low), int(spec.high))
        elif isinstance(spec, HyperoptFloatSpec):
            out[key] = rng.uniform(float(spec.low), float(spec.high))
        elif isinstance(spec, HyperoptCategoricalSpec) and spec.choices:
            out[key] = rng.choice(spec.choices)
    return out


def _merge_flat(base: dict, overlay: dict) -> dict:
    merged = deepcopy(base)
    merged.update(overlay)
    return merged


def _run_simulation(trial_timeout: float) -> subprocess.CompletedProcess[str]:
    cmd: list[str] = [
        sys.executable,
        str(SIMULATE_SCRIPT),
        "--entry",
        str(WORKSPACE / "strategy.py"),
    ]
    return subprocess.run(
        cmd, cwd=str(WORKSPACE), capture_output=True, text=True, timeout=trial_timeout
    )


def main() -> None:
    configure_logging()
    try:
        cfg = _load_params_hyperopt(PARAMS_HYPEROPT_PATH)
    except ValidationError as exc:
        print(f"invalid params-hyperopt.json: {exc}", file=sys.stderr)
        sys.exit(1)
    if cfg is None:
        print("missing or empty params-hyperopt.json", file=sys.stderr)
        sys.exit(1)
    base = _load_json(PARAMS_PATH)
    if not base:
        print("missing or empty params.json", file=sys.stderr)
        sys.exit(1)
    if not cfg.search_space:
        print("params-hyperopt.json needs a non-empty search_space object", file=sys.stderr)
        sys.exit(1)

    n_trials = int(cfg.n_trials)
    wall = 300.0
    maximize = cfg.direction != "minimize"
    metric_key = str(cfg.objective_metric)
    seed = cfg.seed
    rng = random.Random(seed if isinstance(seed, int) else None)
    trial_timeout = float(cfg.trial_timeout_seconds) if cfg.trial_timeout_seconds is not None else 600.0

    t0 = time.perf_counter()
    best_value = float("-inf") if maximize else float("inf")
    best_params: dict | None = None
    completed = 0
    logger.info(
        "hyperopt start: objective=%s direction=%s trials=%s wall=%.3fs trial_timeout=%.3fs seed=%s",
        metric_key,
        "maximize" if maximize else "minimize",
        n_trials,
        wall,
        trial_timeout,
        seed,
    )
    _emit_ui(
        {
            "event": "start",
            "objective_metric": metric_key,
            "maximize": maximize,
            "n_trials": n_trials,
        }
    )
    for i in range(n_trials):
        if time.perf_counter() - t0 >= wall:
            logger.info("stopping early due to wall timeout after %s trials", i)
            _emit_ui(
                {
                    "event": "stopped",
                    "reason": "wall_timeout",
                    "trial": i,
                    "n_trials": n_trials,
                    "objective_metric": metric_key,
                    "best_value": _best_value_for_ui(best_params, best_value),
                    "completed_trials": completed,
                }
            )
            break
        sampled = _sample_from_space(rng, cfg.search_space)
        trial_params = _merge_flat(base, sampled)
        _save_json(PARAMS_PATH, trial_params)
        try:
            proc = _run_simulation(trial_timeout)
        except subprocess.TimeoutExpired:
            logger.info("trial %s/%s sampled=%s outcome=timeout", i + 1, n_trials, sampled)
            _emit_ui(
                {
                    "event": "trial",
                    "trial": i + 1,
                    "n_trials": n_trials,
                    "objective_metric": metric_key,
                    "outcome": "timeout",
                    "best_value": _best_value_for_ui(best_params, best_value),
                    "completed_trials": completed,
                }
            )
            continue
        if proc.returncode != 0:
            logger.info(
                "trial %s/%s sampled=%s outcome=sim_failed returncode=%s stderr_tail=%r",
                i + 1,
                n_trials,
                sampled,
                proc.returncode,
                (proc.stderr or "")[-500:],
            )
            _emit_ui(
                {
                    "event": "trial",
                    "trial": i + 1,
                    "n_trials": n_trials,
                    "objective_metric": metric_key,
                    "outcome": "sim_failed",
                    "best_value": _best_value_for_ui(best_params, best_value),
                    "completed_trials": completed,
                }
            )
            continue
        metrics = _load_json(METRICS_PATH)
        if not metrics:
            logger.info("trial %s/%s sampled=%s outcome=no_metrics", i + 1, n_trials, sampled)
            _emit_ui(
                {
                    "event": "trial",
                    "trial": i + 1,
                    "n_trials": n_trials,
                    "objective_metric": metric_key,
                    "outcome": "no_metrics",
                    "best_value": _best_value_for_ui(best_params, best_value),
                    "completed_trials": completed,
                }
            )
            continue
        value = _nested_get(metrics, metric_key)
        if value is None:
            logger.info(
                "trial %s/%s sampled=%s outcome=missing_objective metric_key=%s",
                i + 1,
                n_trials,
                sampled,
                metric_key,
            )
            _emit_ui(
                {
                    "event": "trial",
                    "trial": i + 1,
                    "n_trials": n_trials,
                    "objective_metric": metric_key,
                    "outcome": "missing_objective",
                    "best_value": _best_value_for_ui(best_params, best_value),
                    "completed_trials": completed,
                }
            )
            continue
        try:
            fv = float(value)
        except (TypeError, ValueError):
            logger.info(
                "trial %s/%s sampled=%s outcome=bad_objective %s=%r",
                i + 1,
                n_trials,
                sampled,
                metric_key,
                value,
            )
            _emit_ui(
                {
                    "event": "trial",
                    "trial": i + 1,
                    "n_trials": n_trials,
                    "objective_metric": metric_key,
                    "outcome": "bad_objective",
                    "best_value": _best_value_for_ui(best_params, best_value),
                    "completed_trials": completed,
                }
            )
            continue
        completed += 1
        better = fv > best_value if maximize else fv < best_value
        if better:
            best_value = fv
            best_params = trial_params
            logger.info(
                "trial %s/%s sampled=%s outcome=completed %s=%s new_best=yes",
                i + 1,
                n_trials,
                sampled,
                metric_key,
                fv,
            )
        else:
            logger.info(
                "trial %s/%s sampled=%s outcome=completed %s=%s new_best=no best_so_far=%s",
                i + 1,
                n_trials,
                sampled,
                metric_key,
                fv,
                best_value,
            )
        _emit_ui(
            {
                "event": "trial",
                "trial": i + 1,
                "n_trials": n_trials,
                "objective_metric": metric_key,
                "outcome": "completed",
                "trial_value": fv,
                "new_best": bool(better),
                "best_value": _best_value_for_ui(best_params, best_value),
                "completed_trials": completed,
            }
        )
    if best_params is None:
        _save_json(PARAMS_PATH, base)
        print("no successful trials; restored params.json to pre-study values", file=sys.stderr)
        sys.exit(1)
    _save_json(PARAMS_PATH, best_params)
    try:
        proc = _run_simulation(trial_timeout)
    except subprocess.TimeoutExpired:
        print("final simulation timed out", file=sys.stderr)
        sys.exit(1)
    if proc.returncode != 0:
        print(
            f"final simulation failed (returncode={proc.returncode}) stderr_tail={proc.stderr!r}",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"best {metric_key}={best_value} over {completed} successful trials")
    logger.info(
        "hyperopt done: best_%s=%s successful_trials=%s elapsed=%.3fs",
        metric_key,
        best_value,
        completed,
        time.perf_counter() - t0,
    )
    _emit_ui(
        {
            "event": "done",
            "objective_metric": metric_key,
            "best_value": _best_value_for_ui(best_params, best_value),
            "completed_trials": completed,
            "n_trials": n_trials,
        }
    )


if __name__ == "__main__":
    main()
