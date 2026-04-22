from __future__ import annotations

import json
import logging
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
    logging.basicConfig(
        level=os.environ.get("HYPEROPT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
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
    wall = float(cfg.timeout_seconds)
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
    for i in range(n_trials):
        if time.perf_counter() - t0 >= wall:
            logger.info("stopping early due to wall timeout after %s trials", i)
            break
        sampled = _sample_from_space(rng, cfg.search_space)
        trial_params = _merge_flat(base, sampled)
        _save_json(PARAMS_PATH, trial_params)
        logger.debug("trial %s/%s sampled=%s", i + 1, n_trials, sampled)
        try:
            proc = _run_simulation(trial_timeout)
        except subprocess.TimeoutExpired:
            logger.debug("trial %s/%s simulation timed out", i + 1, n_trials)
            continue
        if proc.returncode != 0:
            logger.debug(
                "trial %s/%s failed (returncode=%s) stderr_tail=%r",
                i + 1,
                n_trials,
                proc.returncode,
                (proc.stderr or "")[-500:],
            )
            continue
        metrics = _load_json(METRICS_PATH)
        if not metrics:
            logger.debug("trial %s/%s missing or empty metrics.json", i + 1, n_trials)
            continue
        value = _nested_get(metrics, metric_key)
        if value is None:
            logger.debug("trial %s/%s objective metric missing: %s", i + 1, n_trials, metric_key)
            continue
        try:
            fv = float(value)
        except (TypeError, ValueError):
            logger.debug(
                "trial %s/%s objective metric not a number: %s=%r",
                i + 1,
                n_trials,
                metric_key,
                value,
            )
            continue
        completed += 1
        better = fv > best_value if maximize else fv < best_value
        if better:
            best_value = fv
            best_params = trial_params
            logger.info(
                "new best at trial %s/%s: %s=%s sampled=%s",
                i + 1,
                n_trials,
                metric_key,
                fv,
                sampled,
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


if __name__ == "__main__":
    main()
