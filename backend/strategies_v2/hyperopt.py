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
from datetime import datetime, date, timedelta, timezone
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
BACKTEST_PATH = WORKSPACE / "backtest.json"
METRICS_PATH = WORKSPACE / "metrics.json"
WALKFORWARD_PATH = WORKSPACE / "walkforward.json"
STRATEGY_DEADLOCK_EXIT_CODE = 86


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


def _timing_payload(t0: float, finished_steps: int, n_steps: int, timeout_seconds: float) -> dict[str, float]:
    elapsed = max(0.0, time.perf_counter() - t0)
    payload = {
        "elapsed_seconds": elapsed,
        "timeout_seconds": timeout_seconds,
    }
    if finished_steps > 0:
        seconds_per_step = elapsed / finished_steps
        eta_seconds = max(0.0, min(seconds_per_step * n_steps, timeout_seconds) - elapsed)
        payload["seconds_per_step"] = seconds_per_step
        payload["eta_seconds"] = eta_seconds
    return payload


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


def _active_search_space(cfg: ParamsHyperopt) -> dict:
    if not cfg.search_space:
        raise ValueError("params-hyperopt.json needs a non-empty search_space object")

    search_keys = set(cfg.search_space)
    if cfg.included_parameters is not None:
        unknown_included = sorted(set(cfg.included_parameters) - search_keys)
        if unknown_included:
            raise ValueError(
                "included_parameters contains keys not in search_space: "
                + ", ".join(unknown_included)
            )
        active = {key: cfg.search_space[key] for key in cfg.included_parameters}
    else:
        active = dict(cfg.search_space)

    if cfg.excluded_parameters is not None:
        unknown_excluded = sorted(set(cfg.excluded_parameters) - search_keys)
        if unknown_excluded:
            raise ValueError(
                "excluded_parameters contains keys not in search_space: "
                + ", ".join(unknown_excluded)
            )
        excluded = set(cfg.excluded_parameters)
        active = {key: spec for key, spec in active.items() if key not in excluded}

    if not active:
        raise ValueError(
            "params-hyperopt.json active search space is empty after applying "
            "included_parameters/excluded_parameters"
        )
    return active


def _merge_flat(base: dict, overlay: dict) -> dict:
    merged = deepcopy(base)
    merged.update(overlay)
    return merged


def _run_simulation(trial_timeout: float) -> subprocess.CompletedProcess[str]:
    engine = (os.environ.get("STRATEGY_ENGINE") or "python").strip().lower()
    entry_name = "strategy.rs" if engine == "rust" else "strategy.py"
    cmd: list[str] = [
        sys.executable,
        str(SIMULATE_SCRIPT),
        "--entry",
        str(WORKSPACE / entry_name),
    ]
    return subprocess.run(
        cmd, cwd=str(WORKSPACE), capture_output=True, text=True, timeout=trial_timeout
    )


def _run_rust_optimizer() -> int:
    return subprocess.run(
        [
            sys.executable,
            str(SIMULATE_SCRIPT),
            "--entry",
            str(WORKSPACE / "strategy.rs"),
            "--optimize",
        ],
        cwd=str(WORKSPACE),
    ).returncode


def _strategy_deadlock_message(
    *,
    returncode: int | None,
    stdout: str | None,
    stderr: str | None,
    timeout_seconds: float | None = None,
) -> str | None:
    if timeout_seconds is not None:
        return (
            "strategy deadlock suspected: simulation did not finish within "
            f"{timeout_seconds:g}s"
        )
    if returncode == STRATEGY_DEADLOCK_EXIT_CODE:
        detail = (stderr or stdout or "").strip()
        if detail:
            return f"strategy deadlock detected: {detail[-500:]}"
        return "strategy deadlock detected"
    return None


class HyperoptRunError(RuntimeError):
    pass


def _run_simulation_or_raise(trial_timeout: float, *, failure_prefix: str) -> None:
    try:
        proc = _run_simulation(trial_timeout)
    except subprocess.TimeoutExpired as exc:
        msg = _strategy_deadlock_message(
            returncode=None,
            stdout=None,
            stderr=None,
            timeout_seconds=trial_timeout,
        )
        raise HyperoptRunError(str(msg)) from exc
    if proc.returncode == 0:
        return
    deadlock_msg = _strategy_deadlock_message(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
    if deadlock_msg is not None:
        raise HyperoptRunError(deadlock_msg)
    raise HyperoptRunError(
        f"{failure_prefix} failed (returncode={proc.returncode}) stderr_tail={proc.stderr!r}"
    )


def _run_one_study(
    *,
    base: dict,
    cfg: ParamsHyperopt,
    active_space: dict,
    rng: random.Random,
    t0: float,
    trial_timeout: float,
    window_start: date | None = None,
    window_end: date | None = None,
    fold: int | None = None,
    n_folds: int | None = None,
) -> tuple[dict, float, int, int]:
    n_trials = int(cfg.n_trials)
    wall = float(cfg.timeout_seconds)
    maximize = cfg.direction != "minimize"
    metric_key = str(cfg.objective_metric)
    best_value = float("-inf") if maximize else float("inf")
    best_params: dict | None = None
    completed = 0
    attempted = 0

    _emit_ui(
        {
            "event": "start",
            "objective_metric": metric_key,
            "maximize": maximize,
            "n_trials": n_trials,
            "fold": fold,
            "n_folds": n_folds,
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
                    "fold": fold,
                    "n_folds": n_folds,
                    **_timing_payload(t0, i, n_trials, wall),
                }
            )
            break
        attempted = i + 1
        sampled = _sample_from_space(rng, active_space)
        trial_params = _merge_flat(base, sampled)
        if window_start is not None and window_end is not None:
            trial_params["start_date"] = window_start.isoformat()
            trial_params["end_date"] = window_end.isoformat()
        _save_json(PARAMS_PATH, trial_params)
        try:
            proc = _run_simulation(trial_timeout)
        except subprocess.TimeoutExpired as exc:
            msg = _strategy_deadlock_message(
                returncode=None,
                stdout=None,
                stderr=None,
                timeout_seconds=trial_timeout,
            )
            _emit_ui(
                {
                    "event": "stopped",
                    "reason": "strategy_deadlock",
                    "trial": i + 1,
                    "n_trials": n_trials,
                    "objective_metric": metric_key,
                    "message": msg,
                    "best_value": _best_value_for_ui(best_params, best_value),
                    "completed_trials": completed,
                    "fold": fold,
                    "n_folds": n_folds,
                    **_timing_payload(t0, attempted, n_trials, wall),
                }
            )
            raise HyperoptRunError(str(msg)) from exc
        if proc.returncode != 0:
            deadlock_msg = _strategy_deadlock_message(
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
            if deadlock_msg is not None:
                _emit_ui(
                    {
                        "event": "stopped",
                        "reason": "strategy_deadlock",
                        "trial": i + 1,
                        "n_trials": n_trials,
                        "objective_metric": metric_key,
                        "message": deadlock_msg,
                        "best_value": _best_value_for_ui(best_params, best_value),
                        "completed_trials": completed,
                        "fold": fold,
                        "n_folds": n_folds,
                        **_timing_payload(t0, attempted, n_trials, wall),
                    }
                )
                raise HyperoptRunError(deadlock_msg)
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
                    "fold": fold,
                    "n_folds": n_folds,
                    **_timing_payload(t0, attempted, n_trials, wall),
                }
            )
            continue
        metrics = _load_json(METRICS_PATH)
        if not metrics:
            outcome = "no_metrics"
            value = None
        else:
            value = _nested_get(metrics, metric_key)
            outcome = "completed" if value is not None else "missing_objective"
        try:
            fv = float(value) if value is not None else None
        except (TypeError, ValueError):
            fv = None
            outcome = "bad_objective"
        if fv is None:
            logger.info("trial %s/%s sampled=%s outcome=%s", i + 1, n_trials, sampled, outcome)
            _emit_ui(
                {
                    "event": "trial",
                    "trial": i + 1,
                    "n_trials": n_trials,
                    "objective_metric": metric_key,
                    "outcome": outcome,
                    "best_value": _best_value_for_ui(best_params, best_value),
                    "completed_trials": completed,
                    "fold": fold,
                    "n_folds": n_folds,
                    **_timing_payload(t0, attempted, n_trials, wall),
                }
            )
            continue
        completed += 1
        better = fv > best_value if maximize else fv < best_value
        if better:
            best_value = fv
            best_params = trial_params
        logger.info(
            "trial %s/%s sampled=%s outcome=completed %s=%s new_best=%s",
            i + 1,
            n_trials,
            sampled,
            metric_key,
            fv,
            "yes" if better else "no",
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
                "fold": fold,
                "n_folds": n_folds,
                **_timing_payload(t0, attempted, n_trials, wall),
            }
        )

    if best_params is None:
        raise HyperoptRunError("no successful trials")
    return best_params, best_value, completed, attempted


def _parse_date_param(base: dict, key: str) -> date:
    value = base.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HyperoptRunError(f"params.json is missing {key}")
    return date.fromisoformat(value.strip())


def _walk_forward_folds(base: dict, cfg: ParamsHyperopt) -> list[dict]:
    wf = cfg.walk_forward
    if wf is None:
        raise HyperoptRunError("params-hyperopt.json mode='walk_forward' requires walk_forward")
    oos_end = _parse_date_param(base, "end_date")
    oos_start = oos_end - timedelta(days=int(wf.oos_total_days) - 1)
    folds: list[dict] = []
    test_start = oos_start
    while test_start <= oos_end:
        test_end = min(test_start + timedelta(days=int(wf.test_window_days) - 1), oos_end)
        train_end = test_start - timedelta(days=1)
        train_start = train_end - timedelta(days=int(wf.train_window_days) - 1)
        if train_start > train_end:
            raise HyperoptRunError("walk_forward train window is empty")
        folds.append(
            {
                "fold": len(folds) + 1,
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
            }
        )
        test_start = test_start + timedelta(days=int(wf.test_window_days))
    return folds


def _chart_time_to_unix(value: object) -> int | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        v = float(value)
        return int(v / 1000) if v > 1e12 else int(v)
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            if len(s) == 10:
                dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
            else:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
    except ValueError:
        return None
    return None


def _in_date_range_time(value: object, start_d: date, end_d: date) -> bool:
    ut = _chart_time_to_unix(value)
    if ut is None:
        return False
    start_ut = int(datetime.combine(start_d, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    end_ut = int(
        datetime.combine(end_d + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp()
    )
    return start_ut <= ut < end_ut


def _crop_lwc_chart(chart: dict, start_d: date, end_d: date, fold: int) -> dict | None:
    out = deepcopy(chart)
    title = str(out.get("title") or "")
    is_equity = title == "Equity curve vs buy & hold"
    cropped_series = []
    for series in out.get("series") or []:
        if not isinstance(series, dict):
            continue
        s = deepcopy(series)
        data = [
            p for p in s.get("data") or []
            if isinstance(p, dict) and _in_date_range_time(p.get("time"), start_d, end_d)
        ]
        markers = [
            m for m in s.get("markers") or []
            if isinstance(m, dict) and _in_date_range_time(m.get("time"), start_d, end_d)
        ]
        if not data and not markers:
            continue
        s["data"] = data
        if markers:
            s["markers"] = markers
        else:
            s.pop("markers", None)
        if s.get("type") != "Candlestick" and not is_equity:
            label = str(s.get("label") or "")
            s["label"] = f"{label} fold {fold}" if label else f"fold {fold}"
        cropped_series.append(s)
    if not cropped_series:
        return None
    out["series"] = cropped_series
    out.pop("verticalMarkers", None)
    return out


def _crop_table_chart(chart: dict, start_d: date, end_d: date) -> dict | None:
    out = deepcopy(chart)
    rows = []
    for row in out.get("rows") or []:
        if not isinstance(row, dict):
            continue
        t = row.get("time") or row.get("date") or row.get("datetime") or row.get("timestamp")
        if t is not None and _in_date_range_time(t, start_d, end_d):
            rows.append(row)
    if not rows and str(out.get("title") or "") != "Orders":
        return None
    out["rows"] = rows
    return out


def _crop_backtest_doc(doc: dict, start_d: date, end_d: date, fold: int) -> dict:
    out = {
        "strategy_name": doc.get("strategy_name") or "Walk-forward OOS",
        "charts": [],
    }
    if isinstance(doc.get("indicator_series_catalog"), list):
        out["indicator_series_catalog"] = doc["indicator_series_catalog"]
    for chart in doc.get("charts") or []:
        if not isinstance(chart, dict):
            continue
        if chart.get("type") == "lightweight-charts":
            cropped = _crop_lwc_chart(chart, start_d, end_d, fold)
        elif chart.get("type") == "table":
            cropped = _crop_table_chart(chart, start_d, end_d)
        else:
            cropped = None
        if cropped is not None:
            out["charts"].append(cropped)
    return out


def _append_series(dst: dict, src: dict) -> None:
    by_key: dict[tuple[str, str], dict] = {}
    for series in dst.get("series") or []:
        if isinstance(series, dict):
            by_key[(str(series.get("type") or ""), str(series.get("label") or ""))] = series
    for series in src.get("series") or []:
        if not isinstance(series, dict):
            continue
        key = (str(series.get("type") or ""), str(series.get("label") or ""))
        existing = by_key.get(key)
        if existing is None:
            added = deepcopy(series)
            dst.setdefault("series", []).append(added)
            by_key[key] = added
            continue
        existing.setdefault("data", []).extend(deepcopy(series.get("data") or []))
        markers = series.get("markers") or []
        if markers:
            existing.setdefault("markers", []).extend(deepcopy(markers))


def _scale_equity_chart(chart: dict, target_start_equity: float) -> tuple[dict, float]:
    out = deepcopy(chart)
    strategy_series = None
    for series in out.get("series") or []:
        if isinstance(series, dict) and str(series.get("label") or "") == "Strategy equity":
            strategy_series = series
            break
    if not strategy_series:
        return out, target_start_equity
    data = [p for p in strategy_series.get("data") or [] if isinstance(p, dict)]
    if not data:
        return out, target_start_equity
    first = data[0].get("value")
    try:
        first_value = float(first)
    except (TypeError, ValueError):
        return out, target_start_equity
    if first_value <= 0:
        return out, target_start_equity
    scale = target_start_equity / first_value
    next_equity = target_start_equity
    for series in out.get("series") or []:
        if not isinstance(series, dict):
            continue
        for point in series.get("data") or []:
            if not isinstance(point, dict):
                continue
            try:
                point["value"] = float(point["value"]) * scale
            except (TypeError, ValueError, KeyError):
                continue
    last_value = data[-1].get("value")
    try:
        next_equity = float(last_value)
    except (TypeError, ValueError):
        pass
    return out, next_equity


def _stitch_docs(fold_docs: list[dict], fold_infos: list[dict], initial_deposit: float) -> dict:
    final_doc = {
        "strategy_name": f"{fold_docs[0].get('strategy_name') or 'Strategy'} walk-forward OOS",
        "charts": [],
    }
    if isinstance(fold_docs[0].get("indicator_series_catalog"), list):
        final_doc["indicator_series_catalog"] = fold_docs[0]["indicator_series_catalog"]

    chart_by_title: dict[str, dict] = {}
    current_equity = float(initial_deposit)
    for doc in fold_docs:
        for chart in doc.get("charts") or []:
            if not isinstance(chart, dict):
                continue
            title = str(chart.get("title") or "")
            chart_to_add = chart
            if chart.get("type") == "lightweight-charts" and title == "Equity curve vs buy & hold":
                chart_to_add, current_equity = _scale_equity_chart(chart, current_equity)
            existing = chart_by_title.get(title)
            if existing is None:
                existing = deepcopy(chart_to_add)
                final_doc["charts"].append(existing)
                chart_by_title[title] = existing
                continue
            if chart.get("type") == "lightweight-charts":
                _append_series(existing, chart_to_add)
            elif chart.get("type") == "table":
                existing.setdefault("rows", []).extend(deepcopy(chart_to_add.get("rows") or []))

    for chart in final_doc["charts"]:
        if isinstance(chart, dict) and chart.get("type") == "lightweight-charts":
            markers = []
            for idx, info in enumerate(fold_infos):
                try:
                    test_start = date.fromisoformat(str(info["test_start"]))
                    test_end = date.fromisoformat(str(info["test_end"]))
                except (KeyError, ValueError):
                    continue
                marker_time = _first_chart_time_in_range(chart, test_start, test_end)
                if marker_time is None:
                    continue
                markers.append(
                    {
                        "time": marker_time,
                        "label": f"OOS {idx + 1}",
                        "color": "#f59e0b",
                    }
                )
            if markers:
                chart["verticalMarkers"] = markers
    return final_doc


def _first_chart_time_in_range(chart: dict, start_d: date, end_d: date) -> object | None:
    best_time = None
    best_unix = None
    for series in chart.get("series") or []:
        if not isinstance(series, dict):
            continue
        for point in series.get("data") or []:
            if not isinstance(point, dict):
                continue
            t = point.get("time")
            ut = _chart_time_to_unix(t)
            if ut is None or not _in_date_range_time(t, start_d, end_d):
                continue
            if best_unix is None or ut < best_unix:
                best_unix = ut
                best_time = t
    return best_time


def _equity_values_from_doc(doc: dict) -> list[float]:
    for chart in doc.get("charts") or []:
        if not isinstance(chart, dict) or chart.get("title") != "Equity curve vs buy & hold":
            continue
        for series in chart.get("series") or []:
            if isinstance(series, dict) and series.get("label") == "Strategy equity":
                values = []
                for point in series.get("data") or []:
                    try:
                        values.append(float(point["value"]))
                    except (TypeError, ValueError, KeyError):
                        pass
                return values
    return []


def _max_drawdown(equity: list[float]) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    dd = 0.0
    for value in equity:
        if value > peak:
            peak = value
        if peak > 0:
            dd = min(dd, value / peak - 1.0)
    return dd


def _periods_per_year(scale: str) -> float:
    scale = str(scale or "1d").lower()
    if scale == "1d":
        return 252.0
    if scale == "1w":
        return 52.0
    minutes = {"1m": 1, "15m": 15, "1h": 60, "4h": 240}.get(scale, 1440)
    return 252.0 * 6.5 * 60.0 / float(minutes)


def _sharpe_ratio(equity: list[float], scale: str) -> float | None:
    if len(equity) < 3:
        return None
    returns = []
    prev = equity[0]
    for cur in equity[1:]:
        if prev > 0:
            returns.append(cur / prev - 1.0)
        prev = cur
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if var <= 0:
        return None
    return (_periods_per_year(scale) ** 0.5) * mean / (var ** 0.5)


def _orders_from_doc(doc: dict) -> list[dict]:
    for chart in doc.get("charts") or []:
        if isinstance(chart, dict) and chart.get("type") == "table" and chart.get("title") == "Orders":
            return [row for row in chart.get("rows") or [] if isinstance(row, dict)]
    return []


def _win_rate_from_orders(rows: list[dict]) -> float | None:
    open_longs: dict[str, list[float]] = {}
    open_shorts: dict[str, list[float]] = {}
    wins = 0
    closed = 0
    for row in rows:
        if row.get("status") == "invalid":
            continue
        ticker = str(row.get("ticker") or "")
        direction = str(row.get("direction") or "").lower()
        try:
            price = float(row.get("price"))
            before = float(row.get("position_before_order", 0.0))
            after = float(row.get("position_after_order_filled", 0.0))
        except (TypeError, ValueError):
            continue
        if direction == "buy":
            if before < 0:
                entry = open_shorts.setdefault(ticker, []).pop(0) if open_shorts.get(ticker) else None
                if entry is not None:
                    wins += 1 if price < entry else 0
                    closed += 1
            if after > before and before >= 0:
                open_longs.setdefault(ticker, []).append(price)
        elif direction == "sell":
            if before > 0:
                entry = open_longs.setdefault(ticker, []).pop(0) if open_longs.get(ticker) else None
                if entry is not None:
                    wins += 1 if price > entry else 0
                    closed += 1
            if after < before and before <= 0:
                open_shorts.setdefault(ticker, []).append(price)
    return (wins / closed) * 100.0 if closed else None


def _metrics_for_stitched_doc(doc: dict, base: dict) -> dict:
    equity = _equity_values_from_doc(doc)
    initial = float(base.get("initial_deposit") or (equity[0] if equity else 0.0))
    final = equity[-1] if equity else initial
    rows = _orders_from_doc(doc)
    win_rate = _win_rate_from_orders(rows)
    return {
        "total_return": ((final / initial - 1.0) * 100.0) if initial > 0 else None,
        "sharpe_ratio": _sharpe_ratio(equity, str(base.get("scale") or base.get("simulation_scale") or "1d")),
        "max_drawdown": _max_drawdown(equity) * 100.0,
        "win_rate": win_rate,
        "num_trades": sum(1 for row in rows if row.get("status") != "invalid"),
        "final_equity": final,
    }


def _run_single_mode(
    *,
    base: dict,
    cfg: ParamsHyperopt,
    active_space: dict,
    rng: random.Random,
    t0: float,
    trial_timeout: float,
) -> None:
    best_params, best_value, completed, attempted = _run_one_study(
        base=base,
        cfg=cfg,
        active_space=active_space,
        rng=rng,
        t0=t0,
        trial_timeout=trial_timeout,
    )
    _save_json(PARAMS_PATH, best_params)
    _run_simulation_or_raise(trial_timeout, failure_prefix="final simulation")
    print(f"best {cfg.objective_metric}={best_value} over {completed} successful trials")
    _emit_ui(
        {
            "event": "done",
            "objective_metric": str(cfg.objective_metric),
            "best_value": _best_value_for_ui(best_params, best_value),
            "completed_trials": completed,
            "n_trials": int(cfg.n_trials),
            **_timing_payload(t0, attempted, int(cfg.n_trials), float(cfg.timeout_seconds)),
        }
    )


def _run_walk_forward_mode(
    *,
    base: dict,
    cfg: ParamsHyperopt,
    active_space: dict,
    rng: random.Random,
    t0: float,
    trial_timeout: float,
) -> None:
    folds = _walk_forward_folds(base, cfg)
    _emit_ui(
        {
            "event": "walk_forward_start",
            "n_folds": len(folds),
            "n_trials": int(cfg.n_trials),
            "objective_metric": str(cfg.objective_metric),
        }
    )
    fold_docs: list[dict] = []
    fold_infos: list[dict] = []
    for fold in folds:
        fold_no = int(fold["fold"])
        n_folds = len(folds)
        _emit_ui(
            {
                "event": "walk_forward_fold",
                "fold": fold_no,
                "n_folds": n_folds,
                "phase": "train",
                "train_start": fold["train_start"],
                "train_end": fold["train_end"],
                "test_start": fold["test_start"],
                "test_end": fold["test_end"],
                "objective_metric": str(cfg.objective_metric),
            }
        )
        train_base = _merge_flat(
            base,
            {
                "start_date": fold["train_start"].isoformat(),
                "end_date": fold["train_end"].isoformat(),
            },
        )
        fold_t0 = time.perf_counter()
        best_params, best_value, completed, _attempted = _run_one_study(
            base=train_base,
            cfg=cfg,
            active_space=active_space,
            rng=rng,
            t0=fold_t0,
            trial_timeout=trial_timeout,
            window_start=fold["train_start"],
            window_end=fold["train_end"],
            fold=fold_no,
            n_folds=n_folds,
        )

        _emit_ui(
            {
                "event": "walk_forward_fold",
                "fold": fold_no,
                "n_folds": n_folds,
                "phase": "test",
                "objective_metric": str(cfg.objective_metric),
                "best_value": best_value,
                "completed_trials": completed,
            }
        )
        oos_params = _merge_flat(
            best_params,
            {
                "start_date": fold["train_start"].isoformat(),
                "end_date": fold["test_end"].isoformat(),
            },
        )
        _save_json(PARAMS_PATH, oos_params)
        _run_simulation_or_raise(trial_timeout, failure_prefix=f"walk-forward fold {fold_no} OOS simulation")
        raw_doc = _load_json(BACKTEST_PATH)
        cropped = _crop_backtest_doc(raw_doc, fold["test_start"], fold["test_end"], fold_no)
        fold_docs.append(cropped)
        fold_metrics = _metrics_for_stitched_doc(
            _stitch_docs([cropped], [{"test_start": fold["test_start"].isoformat()}], float(base["initial_deposit"])),
            base,
        )
        fold_info = {
            "fold": fold_no,
            "train_start": fold["train_start"].isoformat(),
            "train_end": fold["train_end"].isoformat(),
            "test_start": fold["test_start"].isoformat(),
            "test_end": fold["test_end"].isoformat(),
            "train_objective_metric": str(cfg.objective_metric),
            "train_best_value": best_value,
            "completed_trials": completed,
            "best_params": {
                key: value
                for key, value in best_params.items()
                if base.get(key) != value and key not in {"start_date", "end_date"}
            },
            "oos_metrics": fold_metrics,
        }
        fold_infos.append(fold_info)

    if not fold_docs:
        raise HyperoptRunError("walk-forward produced no OOS folds")
    stitched = _stitch_docs(fold_docs, fold_infos, float(base["initial_deposit"]))
    metrics = _metrics_for_stitched_doc(stitched, base)
    _save_json(BACKTEST_PATH, stitched)
    _save_json(METRICS_PATH, metrics)
    _save_json(
        WALKFORWARD_PATH,
        {
            "mode": "walk_forward",
            "objective_metric": str(cfg.objective_metric),
            "direction": str(cfg.direction),
            "n_trials": int(cfg.n_trials),
            "walk_forward": cfg.walk_forward.model_dump(mode="json") if cfg.walk_forward else None,
            "folds": fold_infos,
            "metrics": metrics,
        },
    )
    print(
        f"walk-forward OOS {cfg.objective_metric}={metrics.get(str(cfg.objective_metric))} "
        f"over {len(folds)} folds"
    )
    _emit_ui(
        {
            "event": "walk_forward_done",
            "objective_metric": str(cfg.objective_metric),
            "n_folds": len(folds),
            "metrics": metrics,
            **_timing_payload(t0, len(folds), len(folds), float(cfg.timeout_seconds)),
        }
    )


def main() -> None:
    configure_logging()
    original_params_text = PARAMS_PATH.read_text(encoding="utf-8") if PARAMS_PATH.is_file() else None
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
    try:
        active_space = _active_search_space(cfg)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    engine = (os.environ.get("STRATEGY_ENGINE") or "python").strip().lower()
    if engine == "rust":
        rust_returncode = _run_rust_optimizer()
        if rust_returncode != 78:
            raise SystemExit(rust_returncode)
        logger.info(
            "Rust optimizer does not support this subscription topology; "
            "falling back to the compatibility optimizer"
        )

    trial_timeout = float(cfg.trial_timeout_seconds) if cfg.trial_timeout_seconds is not None else 1800.0
    rng = random.Random(cfg.seed if isinstance(cfg.seed, int) else None)
    t0 = time.perf_counter()
    logger.info(
        "hyperopt start: mode=%s objective=%s direction=%s trials=%s wall=%.3fs trial_timeout=%.3fs seed=%s",
        cfg.mode,
        cfg.objective_metric,
        "maximize" if cfg.direction != "minimize" else "minimize",
        cfg.n_trials,
        float(cfg.timeout_seconds),
        trial_timeout,
        cfg.seed,
    )

    def _restore_original_params() -> None:
        if original_params_text is not None:
            PARAMS_PATH.write_text(original_params_text, encoding="utf-8")
        else:
            _save_json(PARAMS_PATH, base)

    try:
        if cfg.mode == "walk_forward":
            try:
                _run_walk_forward_mode(
                    base=base,
                    cfg=cfg,
                    active_space=active_space,
                    rng=rng,
                    t0=t0,
                    trial_timeout=trial_timeout,
                )
            finally:
                _restore_original_params()
        else:
            _run_single_mode(
                base=base,
                cfg=cfg,
                active_space=active_space,
                rng=rng,
                t0=t0,
                trial_timeout=trial_timeout,
            )
    except HyperoptRunError as exc:
        if cfg.mode == "walk_forward":
            _restore_original_params()
            print(f"{exc}; restored params.json to pre-study values", file=sys.stderr)
        else:
            _save_json(PARAMS_PATH, base)
            print(f"{exc}; restored params.json to pre-study values", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
