from __future__ import annotations

import json
import random
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

from pydantic import ValidationError

from utils import HyperoptCategoricalSpec, HyperoptFloatSpec, HyperoptIntSpec, ParamsHyperopt

PARAMS_PATH = Path("params.json")
PARAMS_HYPEROPT_PATH = Path("params-hyperopt.json")
METRICS_PATH = Path("metrics.json")


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
            lo = int(spec.low)
            hi = int(spec.high)
            out[key] = rng.randint(lo, hi)
        elif isinstance(spec, HyperoptFloatSpec):
            lo = float(spec.low)
            hi = float(spec.high)
            out[key] = rng.uniform(lo, hi)
        elif isinstance(spec, HyperoptCategoricalSpec) and spec.choices:
            out[key] = rng.choice(spec.choices)
    return out


def _merge_flat(base: dict, overlay: dict) -> dict:
    merged = deepcopy(base)
    merged.update(overlay)
    return merged


def main() -> None:
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
    exe = sys.executable
    t0 = time.perf_counter()
    best_value = float("-inf") if maximize else float("inf")
    best_params: dict | None = None
    completed = 0
    for i in range(n_trials):
        if time.perf_counter() - t0 >= wall:
            break
        sampled = _sample_from_space(rng, cfg.search_space)
        trial_params = _merge_flat(base, sampled)
        _save_json(PARAMS_PATH, trial_params)
        proc = subprocess.run(
            [exe, "strategy.py"],
            cwd=".",
            capture_output=True,
            text=True,
            timeout=trial_timeout,
        )
        if proc.returncode != 0:
            continue
        metrics = _load_json(METRICS_PATH)
        if not metrics:
            continue
        value = _nested_get(metrics, metric_key)
        if value is None:
            continue
        try:
            fv = float(value)
        except (TypeError, ValueError):
            continue
        completed += 1
        better = fv > best_value if maximize else fv < best_value
        if better:
            best_value = fv
            best_params = trial_params
    if best_params is None:
        _save_json(PARAMS_PATH, base)
        print("no successful trials; restored params.json to pre-study values", file=sys.stderr)
        sys.exit(1)
    _save_json(PARAMS_PATH, best_params)
    print(f"best {metric_key}={best_value} over {completed} successful trials")


if __name__ == "__main__":
    main()
