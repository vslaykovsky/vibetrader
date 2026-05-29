from __future__ import annotations
import asyncio
from datetime import datetime, timedelta
import dotenv
dotenv.load_dotenv()
from langsmith import traceable
import json
import logging
import math
import orjson
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import signal
import selectors
from pathlib import Path
from typing import Any, Callable
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from langchain_openrouter import ChatOpenRouter
from pydantic import ValidationError

from application.schemas.hyperopt import (
    ParamsHyperopt,
    ParamsHyperoptOverrides,
    RunHyperoptToolParameters,
)


CHAT_MODEL = os.getenv("CHAT_MODEL", "openai/gpt-5.4")
CHAT_REASONING_EFFORT = os.getenv("CHAT_REASONING_EFFORT", "medium")
OPENROUTER_PROVIDER = {"only": ["OpenAI", "Anthropic"], "allow_fallbacks": False}
CHAT_OPENROUTER_AINVOKE_TIMEOUT_SECONDS = 120
CHAT_OPENROUTER_AINVOKE_TIMEOUT_RETRIES = 3

CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-5.4")
CODEX_REASONING_EFFORT = os.getenv("CODEX_REASONING_EFFORT", "high")


def _codex_bypass_sandbox() -> bool:
    v = (os.getenv("CODEX_BYPASS_SANDBOX") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _clean_codex_thread_id(value: str | None) -> str:
    s = (value or "").strip()
    if not s or len(s) > 128:
        return ""
    if any(ch.isspace() for ch in s):
        return ""
    if any(bad in s for bad in ("..", "/", "\\")):
        return ""
    if not all(ch.isalnum() or ch in ("-", "_") for ch in s):
        return ""
    return s


def _codex_thread_id_from_stdout(stdout: str) -> str:
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        tid = event.get("thread_id")
        if isinstance(tid, str):
            tid = _clean_codex_thread_id(tid)
            if tid:
                return tid
    return ""


def _codex_resume_rollout_missing_error(stdout: str | None, stderr: str | None) -> bool:
    return "no rollout found for thread id" in f"{stdout or ''}\n{stderr or ''}".lower()


def _codex_exec_command(
    task: str,
    root: str,
    resume_thread_id: str,
    sandbox_flag: str,
) -> list[str]:
    cmd = [
        "codex",
        "exec",
        "--json",
        sandbox_flag,
        "-C",
        root,
        "--skip-git-repo-check",
        "-c", "service_tier=fast",
        "-c", f"model={CODEX_MODEL}",
        "-c", "model_verbosity=low",
        "-c", f"model_reasoning_effort={CODEX_REASONING_EFFORT}",
        "-c", "features.fast_mode=true",
    ]
    if resume_thread_id:
        cmd.extend(["resume", resume_thread_id])
    cmd.append(task)
    return cmd

CODE_ANALYSIS_MODEL = os.getenv("CODE_ANALYSIS_MODEL", "anthropic/claude-opus-4.7")
TICKER_SQL_MODEL = os.getenv("TICKER_SQL_MODEL", CODE_ANALYSIS_MODEL)

SYSTEM_PROMPT = f"""You help users design and backtest trading strategies in chat.

Principles
* Reply in the user's language, in plain text unless they ask for another format.
* Be brief after tool runs: summarize only observed results; never invent metrics. The user sees charts and metrics.
* Backtesting is supported; live trading is not.
* Do not reveal generated Python implementation details.
* Today's date is {(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")}.{{user_timezone_line}}

Strategy workflow
* Before the first update_strategy, ASK the user only for missing build/run/visualization details: ticker, scale, start/end dates, indicators, entry/exit, sizing, parameters, charts/visualizations. If the user wants defaults, choose sensible defaults. If they supplied a complete spec, implement it.
* Before the first update_strategy, show all build/run/visualization details in chat and get confirmation from user about these details. For example, show chart list which you want to build and get confirmation from user about these charts.
* If the user clarifies/adds something in details, then loop previous step.
* Pass chart/visualization requirements to the coding agent only when the user explicitly asked for them; never add speculative or diagnostic charts.
* update_strategy delegates implementation to the coding agent in the strategy workspace.
* First update_strategy task: write English instructions with the full user spec because the coding agent may have no prior context. Resumed Codex thread: send a concise delta plus every new or changed requirement; omitted new details are lost.
* For update_strategy tasks, ask for direct implementation of the requested behavior. Do not ask the coding agent to add alternatives, fallback behavior, broad catch-and-continue handlers, fabricated data, mocked results, or hidden invariant recovery; 
* For trainable update_strategy tasks, ask update_strategy to implement support for both exclusive params.json run_mode values, selected at process start: train or test. Do not ask the coding agent to create two active training/testing segments inside one strategy run. Train mode fits and emits trained_model_params, and must not trade. Test mode loads trained_model_params, trades or infers only after loading them, and must not train.
* After successful update_strategy, refresh outputs. For ordinary strategies, call run_backtest. Only call run_hyperopt if the user's current request explicitly asks to optimize, tune, search, or find best strategy parameters. For trainable strategies, run train and test as separate run_backtest calls: first with run_mode="train" and the training date segment, then with run_mode="test" and the test date segment.
* If the user only changes parameters (ticker, dates, thresholds, deposit, etc.), call run_backtest with parameters_json merged into params.json. Do not edit code, add a --params flag, or make strategy.py parse CLI params.
* If a backtest has num_trades=0, say no trades were executed. Do not loosen signals unless code contradicts the user's rules or the user asks.
* If a strategy or hyperopt run reports a strategy deadlock, the likely issue is the strategy.py I/O sequence: the strategy may be reading before the simulator sent input, or the simulator may be waiting because the strategy did not emit the required startup output or per-input time_ack.

Analysis and optimization
* For EDA, market research, or charts without a tradable strategy, use update_strategy then run_backtest.
* For explicit parameter optimization requests, use run_hyperopt. Use parameters_hyperopt_json for study-only overrides and parameters_json for base simulation inputs; follow the run_hyperopt tool schema for allowed study fields. Do not run hyperopt for ordinary strategy creation, strategy edits, parameter changes, EDA, or backtest refreshes.
* If params.json contains run_mode, treat the strategy as trainable and run separate train and out-of-sample test backtests with run_mode/date overrides rather than one combined run.
* For questions about how the latest strategy run performed on historical data, use analyse_run. This includes questions about specific trades, orders, fills, entries/exits, PnL, metrics, dates, bars, or why something happened in the latest backtest. Do not use analyse_code for these.

Canvas and data
* The canvas shows latest run output: interactive price/indicator charts plus emitted panels. Users can pan, zoom, collapse sections, remove with "x", reorder by drag, and hover "?" for per-chart help; UI state is local to that run.
* For hide/remove/reorder requests, explain the canvas controls; no code change is needed. For alignment/spacing requests, explain the layout is already rendered at its best and strategy code is unlikely to help. For more diagnostics, offer to add indicators, series, or panels via strategy output.
* Market data: Alpaca for non-Russian markets, MOEX for Russian markets. Auto-selection is preferred when unsure: try Alpaca, then MOEX. Alpaca data starts in 2016; no pre-2016 data. Never use or suggest yfinance.
* To discover available symbols, use list_tickers with a natural-language query. It can filter by ticker, provider (alpaca/moex), tags such as SNP500, and last_day_volume_usd.
* run_backtest refreshes backtest.json and metrics.json. run_hyperopt optimizes parameters, updates params.json with the best trial, then performs a final run.

{{strategy_help}}"""

STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "strategies_v2"
STRATEGY_AGENTS_TEMPLATE = STRATEGIES_DIR / "AGENTS.md"
STRATEGY_CODE_TEMPLATE = STRATEGIES_DIR / "strategy.py"
STRATEGY_UTILS_TEMPLATE = STRATEGIES_DIR / "utils.py"
STRATEGY_PARAMS_TEMPLATE = STRATEGIES_DIR / "params.json"
STRATEGY_HYPEROPT_TEMPLATE = STRATEGIES_DIR / "hyperopt.py"
SIMULATE_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "simulate_strategy_v2.py"


UPDATE_STRATEGY_TOOL_NAME = "update_strategy"
RUN_BACKTEST_TOOL_NAME = "run_backtest"
RUN_HYPEROPT_TOOL_NAME = "run_hyperopt"
UPDATE_STRATEGY_TOOL_MESSAGE_MAX_JSON = 1024
RUN_EXECUTION_TOOL_MESSAGE_MAX_JSON = 4096
ANALYSE_RUN_TOOL_MESSAGE_MAX_JSON = 4096
ANALYSE_CODE_TOOL_NAME = "analyse_code"
ANALYSE_RUN_TOOL_NAME = "analyse_run"
LIST_TICKERS_TOOL_NAME = "list_tickers"
TICKER_LIST_DEFAULT_LIMIT = 25
TICKER_LIST_MAX_LIMIT = 100
INTERNAL_LIMITS_MESSAGE = "Internal limits were hit. Please try again later."

RUN_HYPEROPT_TOOL_PARAMETERS_SCHEMA = RunHyperoptToolParameters.model_json_schema()

ProgressCallback = Callable[[str], None] | None
TokenCallback = Callable[[str], None] | None

CODING_TOOL = 'codex'

logger = logging.getLogger(__name__)


def _tail(s: str, max_chars: int = 12_000) -> str:
    if len(s) <= max_chars:
        return s
    return s[-max_chars:]


_TRACE_CANVAS_KEYS = frozenset({"canvas", "existing_canvas"})
_TRACE_STRING_MAX_CHARS = 12_000


def _trace_sanitize(value: Any) -> Any:
    if isinstance(value, subprocess.CompletedProcess):
        return {
            "args": value.args,
            "returncode": value.returncode,
            "stdout": _tail(str(value.stdout or ""), _TRACE_STRING_MAX_CHARS),
            "stderr": _tail(str(value.stderr or ""), _TRACE_STRING_MAX_CHARS),
        }
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if str(key) in _TRACE_CANVAS_KEYS or "canvas" in str(key).lower():
                continue
            out[key] = _trace_sanitize(item)
        return out
    if isinstance(value, list):
        return [_trace_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_trace_sanitize(item) for item in value]
    if isinstance(value, str):
        return _tail(value, _TRACE_STRING_MAX_CHARS)
    if callable(value):
        return getattr(value, "__name__", repr(value))
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return repr(value)


_TRACE_INPUTS = lambda inputs: _trace_sanitize(inputs)
_TRACE_OUTPUTS = lambda outputs: _trace_sanitize(outputs)


@traceable(name="run_logged_subprocess", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def _run_logged_subprocess(
    label: str,
    cmd: list[str],
    cwd: str,
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        logger.error("%s timed out after %s seconds", label, e.timeout)
        raise
    except OSError as e:
        logger.error("%s failed to start: %s", label, e)
        raise
    if proc.returncode != 0:
        logger.error(
            "%s failed: returncode=%s\nstdout:\n%s\nstderr:\n%s",
            label,
            proc.returncode,
            _tail(proc.stdout or ""),
            _tail(proc.stderr or ""),
        )
    return proc


def _kill_subprocess_tree(proc: subprocess.Popen[str]) -> None:
    try:
        pgid = os.getpgid(proc.pid)
    except Exception:
        pgid = None
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
    except Exception:
        pass
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.05)
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGKILL)
        else:
            proc.kill()
    except Exception:
        pass


@traceable(name="run_logged_subprocess_stream", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def _run_logged_subprocess_stream(
    label: str,
    cmd: list[str],
    cwd: str,
    *,
    timeout: int,
    on_stderr_line: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    out_chunks: list[str] = []
    err_chunks: list[str] = []
    max_capture_chars = 200_000

    def _append(chunks: list[str], s: str) -> None:
        if not s:
            return
        chunks.append(s)
        total = sum(len(x) for x in chunks)
        while total > max_capture_chars and chunks:
            dropped = chunks.pop(0)
            total -= len(dropped)

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True,
            start_new_session=True,
        )
    except OSError as e:
        logger.error("%s failed to start: %s", label, e)
        raise

    sel = selectors.DefaultSelector()
    assert proc.stdout is not None
    assert proc.stderr is not None
    sel.register(proc.stdout, selectors.EVENT_READ, data="stdout")
    sel.register(proc.stderr, selectors.EVENT_READ, data="stderr")
    t0 = time.monotonic()
    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                try:
                    remaining_out, remaining_err = proc.communicate(timeout=0.2)
                except Exception:
                    remaining_out, remaining_err = "", ""
                _append(out_chunks, remaining_out or "")
                _append(err_chunks, remaining_err or "")
                if on_stderr_line and remaining_err:
                    for seg in remaining_err.splitlines(keepends=True):
                        if seg:
                            on_stderr_line(seg if seg.endswith("\n") else seg + "\n")
                break

            now = time.monotonic()
            if timeout > 0 and (now - t0) > timeout:
                _kill_subprocess_tree(proc)
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

            events = sel.select(timeout=0.25)
            if not events:
                continue

            for key, _mask in events:
                stream_name = key.data
                try:
                    line = key.fileobj.readline()
                except Exception:
                    line = ""
                if not line:
                    continue
                if stream_name == "stdout":
                    _append(out_chunks, line)
                else:
                    _append(err_chunks, line)
                    if on_stderr_line:
                        on_stderr_line(line)
    finally:
        try:
            sel.close()
        except Exception:
            pass

    stdout = "".join(out_chunks)
    stderr = "".join(err_chunks)
    completed = subprocess.CompletedProcess(
        args=cmd, returncode=proc.returncode or 0, stdout=stdout, stderr=stderr
    )
    if completed.returncode != 0:
        logger.error(
            "%s failed: returncode=%s\nstdout:\n%s\nstderr:\n%s",
            label,
            completed.returncode,
            _tail(completed.stdout or ""),
            _tail(completed.stderr or ""),
        )
    return completed


def _hyperopt_status_float_str(v: float) -> str:
    t = f"{v:.3f}"
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t


def _hyperopt_status_duration_str(v: Any) -> str | None:
    try:
        seconds = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    if seconds < 60:
        t = f"{seconds:.1f}".rstrip("0").rstrip(".")
        return f"{t}s"
    minutes = int(seconds // 60)
    rem = int(round(seconds - minutes * 60))
    if rem == 60:
        minutes += 1
        rem = 0
    if minutes < 60:
        return f"{minutes}m {rem}s" if rem else f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def _append_hyperopt_timing_parts(parts: list[str], d: dict[str, Any], *, include_eta: bool = True) -> None:
    per_step = _hyperopt_status_duration_str(d.get("seconds_per_step"))
    if per_step is not None:
        parts.append(f"{per_step}/step")
    if include_eta:
        eta = _hyperopt_status_duration_str(d.get("eta_seconds"))
        if eta is not None:
            parts.append(f"ETA {eta}")


def _hyperopt_ui_line_to_status_text(raw_line: str) -> str | None:
    s = raw_line.strip()
    if not s.startswith("{") or "hyperopt_ui" not in s:
        return None
    try:
        d = json.loads(s)
    except json.JSONDecodeError:
        return None
    if d.get("hyperopt_ui") is not True:
        return None
    ev = d.get("event")
    mk = str(d.get("objective_metric") or "objective")
    nt = d.get("n_trials")
    if ev == "start":
        return f"Hyperopt · {nt} trials · {mk}"[:512]
    if ev == "trial":
        t = d.get("trial")
        n = d.get("n_trials", nt)
        best = d.get("best_value")
        out = str(d.get("outcome") or "")
        parts: list[str] = [f"Hyperopt · trial {t}/{n}"]
        if out == "completed" and d.get("trial_value") is not None:
            try:
                tv = float(d["trial_value"])
                parts.append(f"{mk}={_hyperopt_status_float_str(tv)}")
            except (TypeError, ValueError):
                parts.append(f"{mk}={d.get('trial_value')}")
        if best is not None:
            try:
                bv = float(best)
                parts.append(f"best {mk}={_hyperopt_status_float_str(bv)}")
            except (TypeError, ValueError):
                parts.append(f"best {mk}={best}")
        if out and out != "completed":
            parts.append(out)
        _append_hyperopt_timing_parts(parts, d)
        return " · ".join(parts)[:512]
    if ev == "stopped":
        t = d.get("trial")
        n = d.get("n_trials", nt)
        best = d.get("best_value")
        parts = [f"Hyperopt · stopped at trial {t}/{n}"]
        msg = str(d.get("message") or d.get("reason") or "").strip()
        if msg:
            parts.append(msg)
        if best is not None:
            try:
                parts.append(f"best {mk}={_hyperopt_status_float_str(float(best))}")
            except (TypeError, ValueError):
                parts.append(f"best {mk}={best}")
        _append_hyperopt_timing_parts(parts, d, include_eta=False)
        return " · ".join(parts)[:512]
    if ev == "done":
        best = d.get("best_value")
        c = d.get("completed_trials")
        if best is not None:
            try:
                parts = [
                    "Hyperopt",
                    "done",
                    f"best {mk}={_hyperopt_status_float_str(float(best))} ({c} ok trials)",
                ]
                _append_hyperopt_timing_parts(parts, d, include_eta=False)
                return " · ".join(parts)[:512]
            except (TypeError, ValueError):
                parts = ["Hyperopt", "done", f"best {mk}={best} ({c} ok trials)"]
                _append_hyperopt_timing_parts(parts, d, include_eta=False)
                return " · ".join(parts)[:512]
        parts = ["Hyperopt", f"done ({c} ok trials)"]
        _append_hyperopt_timing_parts(parts, d, include_eta=False)
        return " · ".join(parts)[:512]
    return None


def _simulation_ui_line_to_status_text(raw_line: str) -> str | None:
    s = raw_line.strip()
    if not s.startswith("{") or "simulation_ui" not in s:
        return None
    try:
        d = json.loads(s)
    except json.JSONDecodeError:
        return None
    if d.get("simulation_ui") is not True:
        return None
    ev = d.get("event")
    if ev == "start":
        tickers = d.get("tickers")
        sc = d.get("base_scale") or d.get("strategy_scale") or ""
        units = d.get("total_units")
        tkr = ""
        if isinstance(tickers, list) and tickers:
            tkr = str(tickers[0] or "")
        parts: list[str] = ["Simulation · start"]
        if tkr:
            parts.append(tkr)
        if isinstance(sc, str) and sc.strip():
            parts.append(sc.strip())
        if isinstance(units, int) and units > 0:
            parts.append(f"{units} bars")
        return " · ".join(parts)[:512]
    if ev == "progress":
        pct = d.get("percent")
        done = d.get("completed_units")
        total = d.get("total_units")
        if isinstance(pct, (int, float)):
            p = int(pct)
        else:
            p = None
        parts = ["Simulation"]
        if p is not None:
            parts.append(f"{max(0, min(100, p))}%")
        if isinstance(done, int) and isinstance(total, int) and total > 0:
            parts.append(f"{done}/{total}")
        return " · ".join(parts)[:512]
    if ev == "done":
        return "Simulation · done"[:512]
    return None


def thread_id_allowed(thread_id: str) -> bool:
    tid = (thread_id or "").strip()
    if not tid:
        return False
    if tid != thread_id:
        return False
    for bad in ("..", "/", "\\"):
        if bad in tid:
            return False
    return True


def _chmod_readonly(path: Path) -> None:
    try:
        path.chmod(0o444)
    except OSError:
        logger.warning("could not set read-only mode on %s", path)


def ensure_strategy_workspace(thread_id: str) -> Path:
    if not thread_id_allowed(thread_id):
        raise ValueError("invalid thread_id")
    workspace = STRATEGIES_DIR / thread_id
    workspace.mkdir(parents=True, exist_ok=True)
    dest_agents = workspace / "AGENTS.md"
    if not dest_agents.is_file() and STRATEGY_AGENTS_TEMPLATE.is_file():
        shutil.copy2(STRATEGY_AGENTS_TEMPLATE, dest_agents)
    dest_strategy = workspace / "strategy.py"
    if not dest_strategy.is_file() and STRATEGY_CODE_TEMPLATE.is_file():
        shutil.copy2(STRATEGY_CODE_TEMPLATE, dest_strategy)
    dest_params = workspace / "params.json"
    if not dest_params.is_file() and STRATEGY_PARAMS_TEMPLATE.is_file():
        shutil.copy2(STRATEGY_PARAMS_TEMPLATE, dest_params)
    for template, name in (
        (STRATEGY_UTILS_TEMPLATE, "utils.py"),
        (STRATEGY_HYPEROPT_TEMPLATE, "hyperopt.py"),
    ):
        if not template.is_file():
            continue
        dest = workspace / name
        if dest.is_file():
            try:
                dest.chmod(0o644)
            except OSError:
                logger.warning("could not make writable for refresh: %s", dest)
        shutil.copy2(template, dest)
        _chmod_readonly(dest)
    return workspace


def strategy_root_for_thread(thread_id: str) -> Path:
    return ensure_strategy_workspace(thread_id)


def read_strategy_code(thread_id: str) -> str:
    root = ensure_strategy_workspace(thread_id)
    path = root / "strategy.py"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def read_strategy_utils(thread_id: str) -> str:
    root = ensure_strategy_workspace(thread_id)
    path = root / "utils.py"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def restore_strategy_workspace_from_snapshot(
    thread_id: str,
    *,
    code: str | None,
    canvas: dict[str, Any] | None,
) -> None:
    root = ensure_strategy_workspace(thread_id)

    (root / "strategy.py").write_text(code or "", encoding="utf-8")

    for name in CANVAS_OUTPUT_FILES:
        path = root / name
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass

    if not isinstance(canvas, dict):
        return
    output = canvas.get("output")
    if not isinstance(output, dict):
        return

    for filename, contents in output.items():
        if not isinstance(filename, str) or filename not in CANVAS_OUTPUT_FILES:
            continue
        out_path = root / filename
        if isinstance(contents, (dict, list)) and out_path.suffix.lower() == ".json":
            out_path.write_text(json.dumps(contents, indent=2, sort_keys=True), encoding="utf-8")
        elif isinstance(contents, str):
            out_path.write_text(contents, encoding="utf-8")
        else:
            out_path.write_text(str(contents), encoding="utf-8")

AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": LIST_TICKERS_TOOL_NAME,
            "description": (
                "List available market tickers from the tickers database table. "
                "Pass a natural-language query such as 'most liquid S&P 500 stocks', "
                "'MOEX tickers starting with SB', or 'Alpaca crypto symbols'. "
                "The tool generates and validates a read-only SQL SELECT on the fly. "
                "Known providers and tags are taken from scripts/sync_tickers.py."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Natural-language criteria for filtering or ranking tickers. "
                            "Available fields are ticker, provider, tags, updated_at, and last_day_volume_usd. "
                            "Known providers: alpaca, moex. Known tags: stock, crypto, SNP500."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": TICKER_LIST_MAX_LIMIT,
                        "description": f"Maximum rows to return. Defaults to {TICKER_LIST_DEFAULT_LIMIT}.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": UPDATE_STRATEGY_TOOL_NAME,
            "description": (
                "Edit this thread's strategy workspace with the coding agent. "
                "After success, call run_backtest to refresh outputs unless the current user request explicitly asks "
                "to optimize parameters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "English instructions for the coding agent. For a first task, include the full user spec "
                            "(rules, indicators, params, entry/exit, sizing, constraints, instruments, dates, edge cases). "
                            "On a resumed Codex thread, a delta is enough, but include every new or changed requirement. "
                            "Request a direct implementation; do not request alternatives, fallback behavior, broad "
                            "catch-and-continue handlers, fabricated data, mocked results, or hidden invariant recovery. "
                            "For trainable strategies, ask for support for both exclusive params.json run_mode values, "
                            "selected at process start: train or test. Do not ask the coding agent to create two active "
                            "training/testing segments inside one strategy run; train and test date windows are applied "
                            "later through separate run_backtest calls. "
                            "Do not include file paths or filenames; workspace layout is fixed."
                        ),
                    }
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": RUN_BACKTEST_TOOL_NAME,
            "description": (
                "Run one backtest with no code edits. It uses params.json, streams OHLC bars to strategy.py, "
                "refreshes backtest.json and metrics.json, and can merge optional parameters_json into params.json first. "
                "If num_trades=0, tell the user no trades were executed; do not assume a bug."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "parameters_json": {
                        "type": "string",
                        "description": (
                            "Optional valid JSON merged into params.json before the run. Objects merge recursively; "
                            "lists merge by index; scalars, type mismatches, and non-object JSON replace existing values."
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": RUN_HYPEROPT_TOOL_NAME,
            "description": (
                "Run hyperparameter optimization with no code edits only when the current user request explicitly asks "
                "to optimize, tune, search, or find best strategy parameters. Requires params-hyperopt.json, runs simulator "
                "trials, writes best params to params.json, then performs a final run. parameters_json changes base inputs; "
                "parameters_hyperopt_json changes the study definition."
            ),
            "parameters": RUN_HYPEROPT_TOOL_PARAMETERS_SCHEMA,
        },
    },
    {
        "type": "function",
        "function": {
            "name": ANALYSE_RUN_TOOL_NAME,
            "description": (
                "Use Codex to answer questions about how the latest backtest or historical strategy run performed. "
                "Use this for specific trades, orders, fills, entries/exits, PnL, metrics, dates, bars, or why a runtime "
                "event happened. It inspects run output files; no file edits."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": (
                            "A natural-language question about latest run output, historical performance, specific dates, "
                            "trades, fills, bars, PnL, or metrics."
                        ),
                    }
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ANALYSE_CODE_TOOL_NAME,
            "description": (
                "Answer quick questions about this thread's current strategy.py logic and params only. "
                "No file edits; not for historical performance, specific trades, dates, simulator, portfolio, fills, "
                "metrics, run output, or platform behavior."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "A natural-language question about the current strategy.py logic or params only.",
                    }
                },
                "required": ["question"],
            },
        },
    },
]


def _trim_tool_payload_streams(
    payload: dict[str, Any],
    max_json_len: int,
    out_key: str,
    err_key: str,
) -> dict[str, Any]:
    out = dict(payload)
    if max_json_len <= 0:
        out[out_key] = ""
        out[err_key] = ""
        return out
    out[out_key] = _tail(str(out.get(out_key, "") or ""), max_json_len)
    out[err_key] = _tail(str(out.get(err_key, "") or ""), max_json_len)
    return out


def _read_strategy_params_text(thread_id: str) -> str:
    root = ensure_strategy_workspace(thread_id)
    params_path = root / "params.json"
    if not params_path.is_file():
        return ""
    try:
        return params_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


@traceable(name="run_analyse_code", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def run_analyse_code(
    *,
    thread_id: str,
    question: str,
) -> dict[str, Any]:
    q = (question or "").strip()
    if not q:
        return {"ok": False, "error": "question is empty"}
    if not thread_id_allowed(thread_id):
        return {"ok": False, "error": "invalid thread_id"}
    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        return {"ok": False, "error": "OPENROUTER_API_KEY is not configured"}

    code = read_strategy_code(thread_id)
    utils_text = read_strategy_utils(thread_id)
    params_text = _read_strategy_params_text(thread_id)

    analysis_system = (
        "Answer only questions about the provided strategy.py logic and params. "
        "Use utils.py only for imported data models. Do not infer simulator, portfolio, fills, metrics, "
        "or platform behavior; say those are outside this tool's context. "
        "Use 1-4 sentences. If unknown, say what is missing."
    )
    context = (
        "Strategy params (JSON, may be empty):\n"
        f"{params_text if params_text else ''}\n\n"
        "utils.py (Python, may be empty):\n"
        f"{utils_text if utils_text else ''}\n\n"
        "strategy.py (Python, may be empty):\n"
        f"{code if code else ''}"
    )

    llm = ChatOpenRouter(
        model=CODE_ANALYSIS_MODEL,
        request_timeout=120_000,
        openrouter_provider=OPENROUTER_PROVIDER,
    )
    msg = _run_chat_openrouter_ainvoke(
        llm,
        [
            SystemMessage(content=analysis_system),
            SystemMessage(content=context),
            HumanMessage(content=q),
        ],
        timeout_seconds=120,
    )
    answer = _aimessage_plain_text(msg).strip()
    return {"ok": True, "answer": answer}


def _codex_content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
                else:
                    nested = block.get("content")
                    if isinstance(nested, (str, list)):
                        parts.append(_codex_content_text(nested))
        return "".join(parts)
    return ""


def _codex_event_answer_text(event: dict[str, Any]) -> str:
    item = event.get("item")
    if isinstance(item, dict):
        item_type = str(item.get("type") or "")
        role = str(item.get("role") or "")
        if item_type in ("agent_message", "assistant_message") or (
            item_type == "message" and role == "assistant"
        ):
            text = _codex_content_text(item.get("content"))
            if text:
                return text
            text = _codex_content_text(item.get("text"))
            if text:
                return text
    message = event.get("message")
    if isinstance(message, dict):
        role = str(message.get("role") or "")
        if not role or role == "assistant":
            text = _codex_content_text(message.get("content"))
            if text:
                return text
            text = _codex_content_text(message.get("text"))
            if text:
                return text
    event_type = str(event.get("type") or "")
    if event_type in ("agent_message", "assistant_message"):
        text = _codex_content_text(event.get("content"))
        if text:
            return text
        text = _codex_content_text(event.get("text"))
        if text:
            return text
    for key in ("answer", "final_answer", "output"):
        text = _codex_content_text(event.get(key))
        if text:
            return text
    return ""


def _codex_stdout_final_answer(stdout: str) -> str:
    answers: list[str] = []
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        text = _codex_event_answer_text(event).strip()
        if text:
            answers.append(text)
    return answers[-1].strip() if answers else ""


def _codex_analyse_run_task(question: str) -> str:
    return f"""Answer the user's question by inspecting this strategy workspace and the latest run output.

User question:
{question}

Rules:
- Do not edit, create, delete, or rename files.
- Use actual workspace data such as params.json, backtest.json, metrics.json, strategy.py, utils.py, trained_model_params.json, and emitted output JSON.
- This is a historical performance analysis request: trades, fills, entries, exits, PnL, metrics, dates, bars, indicator values, or why a runtime event happened.
- You may run read-only shell or Python commands to inspect files.
- If the necessary output data is missing, say exactly what is missing and do not guess.
- Respond directly and concisely in plain text.
"""


@traceable(name="run_analyse_run", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def run_analyse_run(
    *,
    thread_id: str,
    question: str,
    on_progress: ProgressCallback = None,
    codex_thread_id: str | None = None,
) -> dict[str, Any]:
    q = (question or "").strip()
    if not q:
        return {"ok": False, "error": "question is empty"}
    if not thread_id_allowed(thread_id):
        return {"ok": False, "error": "invalid thread_id"}
    root = ensure_strategy_workspace(thread_id)
    if on_progress:
        on_progress("Analyzing latest run output, this may take a few minutes…")
    existing_codex_thread_id = _clean_codex_thread_id(codex_thread_id)
    proc = _run_codex_exec(
        _codex_analyse_run_task(q),
        root,
        codex_thread_id=existing_codex_thread_id,
    )
    next_codex_thread_id = _codex_thread_id_from_stdout(proc.stdout or "") or existing_codex_thread_id
    answer = _codex_stdout_final_answer(proc.stdout or "")
    result: dict[str, Any] = {
        "runner": "codex",
        "codex_returncode": proc.returncode,
        "codex_stdout": _tail(proc.stdout or ""),
        "codex_stderr": _tail(proc.stderr or ""),
        "codex_thread_id": next_codex_thread_id,
        "ok": proc.returncode == 0,
    }
    if answer:
        result["answer"] = answer
    if proc.returncode != 0:
        combined_output = f"{proc.stdout or ''}\n{proc.stderr or ''}"
        if _coding_agent_usage_limit_error(combined_output):
            result["error"] = INTERNAL_LIMITS_MESSAGE
            result["terminal"] = True
        else:
            result["error"] = "codex exec failed"
    return result


_TICKER_SQL_BLOCKED_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|execute|merge|vacuum|attach|detach|pragma|union|intersect|except)\b",
    re.IGNORECASE,
)
_TICKER_SQL_TABLE_REF_RE = re.compile(
    r'\bfrom\s+((?:"[^"]+"|[A-Za-z_][\w]*)(?:\.(?:"[^"]+"|[A-Za-z_][\w]*))?)',
    re.IGNORECASE,
)


def _coerce_ticker_limit(value: Any) -> int:
    if value is None or value == "":
        return TICKER_LIST_DEFAULT_LIMIT
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return TICKER_LIST_DEFAULT_LIMIT
    return max(1, min(TICKER_LIST_MAX_LIMIT, limit))


def _strip_sql_response(raw: str) -> str:
    s = (raw or "").strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _sql_identifier_tail(ref: str) -> str:
    part = (ref or "").split(".")[-1].strip()
    if part.startswith('"') and part.endswith('"') and len(part) >= 2:
        part = part[1:-1]
    return part.lower()


def _normalize_ticker_listing_sql(sql: str) -> str:
    s = _strip_sql_response(sql)
    s = re.sub(r"\s+", " ", s).strip()
    if s.endswith(";"):
        s = s[:-1].strip()
    if not s:
        raise ValueError("generated SQL is empty")
    if ";" in s:
        raise ValueError("generated SQL must contain one statement")
    if ":" in s:
        raise ValueError("generated SQL must use literal values, not bind parameters")
    if "--" in s or "/*" in s or "*/" in s:
        raise ValueError("generated SQL must not contain comments")
    if not re.match(r"^select\b", s, re.IGNORECASE):
        raise ValueError("generated SQL must be a SELECT statement")
    if _TICKER_SQL_BLOCKED_RE.search(s):
        raise ValueError("generated SQL contains a blocked keyword")
    if re.search(r"\bjoin\b", s, re.IGNORECASE):
        raise ValueError("generated SQL must not use joins")
    if re.search(r"\(\s*select\b", s, re.IGNORECASE):
        raise ValueError("generated SQL must not use subqueries")
    from_sections = re.findall(
        r"\bfrom\s+(.+?)(?=\bwhere\b|\bgroup\s+by\b|\border\s+by\b|\blimit\b|$)",
        s,
        re.IGNORECASE,
    )
    if len(from_sections) != 1:
        raise ValueError("generated SQL must read from tickers once")
    if "," in from_sections[0]:
        raise ValueError("generated SQL may only read from tickers")
    refs = _TICKER_SQL_TABLE_REF_RE.findall(s)
    if len(refs) != 1:
        raise ValueError("generated SQL must read from tickers")
    for ref in refs:
        if _sql_identifier_tail(ref) != "tickers":
            raise ValueError("generated SQL may only read from tickers")
    return s


def _default_session_factory() -> Callable[[], Any]:
    from db.session import SessionLocal

    return SessionLocal


def _normalize_ticker_result_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key in ("ticker", "provider") and value is not None:
            out[key] = str(value)
        elif key == "tags":
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    parsed = value
                out[key] = parsed
            else:
                out[key] = value
        elif key == "last_day_volume_usd" and value is not None:
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                out[key] = value
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _ticker_sql_prompt_vocabulary() -> tuple[list[str], list[str]]:
    try:
        from scripts import sync_tickers

        providers = [
            sync_tickers._PROVIDER_ALPACA,
            sync_tickers._PROVIDER_MOEX,
        ]
        tags = [
            sync_tickers._STOCK_TAG,
            sync_tickers._CRYPTO_TAG,
            sync_tickers._SNP500_TAG,
        ]
    except Exception:
        providers = ["alpaca", "moex"]
        tags = ["stock", "crypto", "SNP500"]
    return sorted({str(v) for v in providers if str(v)}), sorted({str(v) for v in tags if str(v)})


@traceable(name="generate_ticker_listing_sql", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def _generate_ticker_listing_sql(*, query: str, limit: int) -> str:
    try:
        from db.session import engine

        dialect = engine.dialect.name
    except Exception:
        dialect = "postgresql"
    providers, tags = _ticker_sql_prompt_vocabulary()
    system = (
        "Generate exactly one read-only SQL SELECT statement for listing market tickers. "
        "Use only the table tickers with columns ticker, provider, tags, updated_at, last_day_volume_usd. "
        f"The only possible provider values are: {', '.join(repr(v) for v in providers)}. "
        f"The only possible tags values are: {', '.join(repr(v) for v in tags)}. "
        "Return columns useful to the user, usually ticker, provider, tags, last_day_volume_usd. "
        "Do not use joins, CTEs, subqueries, comments, semicolons, bind parameters, DDL, or DML. "
        "Use LOWER(...) LIKE for text matching and CAST(tags AS TEXT) for tag matching. "
        "For stock requests, match LOWER(CAST(tags AS TEXT)) LIKE '%stock%'. "
        "For crypto requests, match LOWER(CAST(tags AS TEXT)) LIKE '%crypto%'. "
        "For S&P 500 or SNP500 requests, match CAST(tags AS TEXT) LIKE '%SNP500%'. "
        "For MOEX or Russian requests, filter provider = 'moex'. "
        "For Alpaca or US requests, filter provider = 'alpaca'. "
        "For liquid, popular, or high-volume requests, order by last_day_volume_usd IS NULL, last_day_volume_usd DESC. "
        f"Use SQL compatible with {dialect}. Include LIMIT {limit}. Return only SQL."
    )
    llm = ChatOpenRouter(
        model=TICKER_SQL_MODEL,
        request_timeout=60_000,
        openrouter_provider=OPENROUTER_PROVIDER,
    )
    msg = _run_chat_openrouter_ainvoke(
        llm,
        [
            SystemMessage(content=system),
            HumanMessage(content=(query or "").strip()),
        ],
        timeout_seconds=60,
    )
    return _normalize_ticker_listing_sql(_aimessage_plain_text(msg))


@traceable(name="execute_ticker_listing_sql", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def _execute_ticker_listing_sql(
    sql: str,
    *,
    limit: Any = None,
    session_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    max_rows = _coerce_ticker_limit(limit)
    safe_sql = _normalize_ticker_listing_sql(sql)
    from sqlalchemy import text

    SessionFactory = session_factory or _default_session_factory()
    session = SessionFactory()
    try:
        rows = session.execute(
            text(f"SELECT * FROM ({safe_sql}) AS ticker_listing LIMIT :ticker_listing_limit"),
            {"ticker_listing_limit": max_rows},
        ).mappings().all()
    finally:
        session.close()
    normalized = [_normalize_ticker_result_row(dict(row)) for row in rows]
    tickers = [
        str(row["ticker"])
        for row in normalized
        if isinstance(row.get("ticker"), str) and row.get("ticker")
    ]
    return {
        "ok": True,
        "sql": safe_sql,
        "row_count": len(normalized),
        "rows": normalized,
        "tickers": tickers,
    }


@traceable(name="run_list_tickers", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def run_list_tickers(*, query: str, limit: Any = None) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "query is empty"}
    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        return {"ok": False, "error": "OPENROUTER_API_KEY is not configured"}
    max_rows = _coerce_ticker_limit(limit)
    try:
        sql = _generate_ticker_listing_sql(query=q, limit=max_rows)
        payload = _execute_ticker_listing_sql(sql, limit=max_rows)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    payload["query"] = q
    payload["limit"] = max_rows
    return payload


@traceable(name="generate_strategy_algorithm_pseudocode", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def generate_strategy_algorithm_pseudocode(*, code: str, language: str = "") -> dict[str, Any]:
    src = (code or "").strip()
    if not src:
        return {
            "ok": True,
            "algorithm": "No strategy source code was saved for this run.",
        }
    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        return {"ok": False, "error": "OPENROUTER_API_KEY is not configured"}

    lang = (language or "").strip().lower()
    lang_line = (
        f"Write natural-language text, step titles, and comments in ISO 639-1 language {lang}."
        if lang
        else "Write natural-language text, step titles, and comments in English."
    )
    system = (
        "Write compact, language-agnostic pseudocode for only the strategy's core trading logic. "
        "Omit CLI/imports/logging/I/O/HTTP/dataframe plumbing/plotting/JSON/chart serialization/generic helpers unless they encode a trading rule. "
        "For standard indicators (RSI, MACD, etc.), mention use without outlining internal computation. "
        "Avoid long code quotes; prefer tight numbered steps or bullets. If essential logic is ambiguous, give the most likely interpretation in one line. "
        "Markdown is allowed. "
        + lang_line
    )
    llm = ChatOpenRouter(
        model=CODE_ANALYSIS_MODEL,
        request_timeout=120_000,
        openrouter_provider=OPENROUTER_PROVIDER,
    )
    msg = _run_chat_openrouter_ainvoke(
        llm,
        [
            SystemMessage(content=system),
            HumanMessage(
                content="Extract core-algorithm pseudocode from this strategy.py (business logic only, no I/O boilerplate).\n\n"
                + src
            ),
        ],
        timeout_seconds=120,
    )
    text = _aimessage_plain_text(msg).strip()
    return {"ok": True, "algorithm": text or "(empty response)"}


CANVAS_OUTPUT_FILES: frozenset[str] = frozenset(
    {
        "params.json",
        "backtest.json",
        "metrics.json",
        "params-hyperopt.json",
        "trained_model_params.json",
    }
)

CANVAS_OUTPUT_KEYS: frozenset[str] = CANVAS_OUTPUT_FILES | frozenset({"strategy_cli_description"})


def _read_strategy_workspace_files(thread_id: str) -> dict[str, Any]:
    root = strategy_root_for_thread(thread_id)
    out: dict[str, Any] = {}
    for name in CANVAS_OUTPUT_FILES:
        path = root / name
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() == ".json":
            try:
                out[name] = json.loads(raw)
            except json.JSONDecodeError:
                out[name] = raw
        else:
            out[name] = raw
    return out


def sanitize_json_for_postgres(value: Any) -> Any:
    return orjson.loads(
        orjson.dumps(value, option=orjson.OPT_SERIALIZE_NUMPY)
    )


def canvas_with_output(existing_canvas: dict[str, Any], thread_id: str) -> dict[str, Any]:
    merged = dict(existing_canvas)
    if thread_id_allowed(thread_id):
        existing_output = merged.get("output")
        if not isinstance(existing_output, dict):
            existing_output = {}
        filtered_existing = {k: v for k, v in existing_output.items() if k in CANVAS_OUTPUT_KEYS}
        disk_output = _read_strategy_workspace_files(thread_id)
        merged["output"] = {**filtered_existing, **disk_output}
        workspace = strategy_root_for_thread(thread_id)
        desc = read_strategy_description_from_workspace(workspace)
        out = merged["output"]
        if desc:
            out["strategy_cli_description"] = desc
        else:
            out.pop("strategy_cli_description", None)
    else:
        merged["output"] = {}
    return sanitize_json_for_postgres(merged)


@traceable(name="run_codex_exec", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def _run_codex_exec(
    task: str,
    cwd: Path,
    codex_thread_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    root = str(cwd.resolve())
    resume_thread_id = _clean_codex_thread_id(codex_thread_id)
    sandbox_flag = (
        "--dangerously-bypass-approvals-and-sandbox"
        if _codex_bypass_sandbox()
        else "--full-auto"
    )
    cmd = _codex_exec_command(task, root, resume_thread_id, sandbox_flag)
    proc = _run_logged_subprocess("codex exec", cmd, root, timeout=600)
    if (
        resume_thread_id
        and proc.returncode != 0
        and _codex_resume_rollout_missing_error(proc.stdout, proc.stderr)
    ):
        logger.warning(
            "codex resume rollout missing; retrying without resume",
            extra={"codex_thread_id": resume_thread_id},
        )
        retry_cmd = _codex_exec_command(task, root, "", sandbox_flag)
        return _run_logged_subprocess("codex exec retry without resume", retry_cmd, root, timeout=600)
    return proc


@traceable(name="run_claude_exec", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def _run_claude_exec(task: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    cmd: list[str] = ['claude', '--output-format', 'json', '--permission-mode', 'bypassPermissions']
    cmd.extend(["-p", task ])
    return _run_logged_subprocess("claude", cmd, str(cwd), timeout=600)


def _run_coding_agent_exec(
    task: str,
    cwd: Path,
    codex_thread_id: str | None = None,
) -> tuple[str, subprocess.CompletedProcess[str]]:
    tool = CODING_TOOL    
    if tool == "claude":
        return tool, _run_claude_exec(task, cwd)
    return tool, _run_codex_exec(task, cwd, codex_thread_id=codex_thread_id)


@traceable(name="run_simulation", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def _run_simulation(*, workspace: Path) -> subprocess.CompletedProcess[str]:
    cmd: list[str] = [
        sys.executable,
        str(SIMULATE_SCRIPT_PATH),
        "--entry",
        str(workspace / "strategy.py"),
    ]
    timeout_s = int(os.getenv("STRATEGY_BACKTEST_TIMEOUT_S", "1800"))
    freeze_timeout_s = int(os.getenv("STRATEGY_BACKTEST_FREEZE_TIMEOUT_S", "120"))
    return _run_logged_subprocess_with_freeze_watchdog(
        label="strategy simulation",
        cmd=cmd,
        cwd=str(workspace),
        timeout=timeout_s,
        freeze_timeout=freeze_timeout_s,
        on_stderr_line=None,
    )


@traceable(name="run_logged_subprocess_with_freeze_watchdog", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def _run_logged_subprocess_with_freeze_watchdog(
    *,
    label: str,
    cmd: list[str],
    cwd: str,
    timeout: int,
    freeze_timeout: int,
    on_stderr_line: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    if freeze_timeout <= 0:
        return _run_logged_subprocess(label, cmd, cwd, timeout=timeout)

    t0 = time.monotonic()
    last_progress = t0
    out_chunks: list[str] = []
    err_chunks: list[str] = []
    max_capture_chars = 200_000

    def _append(chunks: list[str], s: str) -> None:
        if not s:
            return
        chunks.append(s)
        total = sum(len(x) for x in chunks)
        while total > max_capture_chars and chunks:
            dropped = chunks.pop(0)
            total -= len(dropped)

    def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
        try:
            pgid = os.getpgid(proc.pid)
        except Exception:
            pgid = None
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGTERM)
            else:
                proc.terminate()
        except Exception:
            pass
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.05)
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True,
            start_new_session=True,
        )
    except OSError as e:
        logger.error("%s failed to start: %s", label, e)
        raise

    sel = selectors.DefaultSelector()
    assert proc.stdout is not None
    assert proc.stderr is not None
    sel.register(proc.stdout, selectors.EVENT_READ, data="stdout")
    sel.register(proc.stderr, selectors.EVENT_READ, data="stderr")

    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                try:
                    remaining_out, remaining_err = proc.communicate(timeout=0.2)
                except Exception:
                    remaining_out, remaining_err = "", ""
                _append(out_chunks, remaining_out or "")
                _append(err_chunks, remaining_err or "")
                break

            now = time.monotonic()
            if timeout > 0 and (now - t0) > timeout:
                _kill_process_tree(proc)
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

            if (now - last_progress) > freeze_timeout:
                _kill_process_tree(proc)
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=freeze_timeout)

            events = sel.select(timeout=0.25)
            if not events:
                continue

            for key, _mask in events:
                stream_name = key.data
                try:
                    line = key.fileobj.readline()
                except Exception:
                    line = ""
                if not line:
                    continue
                last_progress = time.monotonic()
                if stream_name == "stdout":
                    _append(out_chunks, line)
                else:
                    _append(err_chunks, line)
                    if on_stderr_line:
                        try:
                            on_stderr_line(line)
                        except Exception:
                            pass
    finally:
        try:
            sel.close()
        except Exception:
            pass

    stdout = "".join(out_chunks)
    stderr = "".join(err_chunks)
    completed = subprocess.CompletedProcess(args=cmd, returncode=proc.returncode or 0, stdout=stdout, stderr=stderr)
    if completed.returncode != 0:
        logger.error(
            "%s failed: returncode=%s\nstdout:\n%s\nstderr:\n%s",
            label,
            completed.returncode,
            _tail(completed.stdout or ""),
            _tail(completed.stderr or ""),
        )
    return completed


@traceable(name="run_workspace_command", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def _run_workspace_command(
    command: str,
    cwd: Path,
    *,
    on_stderr_line: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    parts = shlex.split(command)
    if parts and parts[0] == "python":
        parts[0] = sys.executable
    is_hyperopt = bool(parts and parts[-1] == "hyperopt.py")
    timeout_env = "STRATEGY_HYPEROPT_TIMEOUT_S" if is_hyperopt else "STRATEGY_BACKTEST_TIMEOUT_S"
    timeout_default = "25200" if is_hyperopt else "1800"
    timeout_s = int(os.getenv(timeout_env, timeout_default))
    if on_stderr_line is not None:
        return _run_logged_subprocess_stream(
            "workspace command",
            parts,
            str(cwd),
            timeout=timeout_s,
            on_stderr_line=on_stderr_line,
        )
    return _run_logged_subprocess("workspace command", parts, str(cwd), timeout=timeout_s)


def read_strategy_name_from_workspace(root: Path) -> str:
    candidates = (
        root / "params.json",
        root / "backtest.json",
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8").strip()
            if not raw:
                continue
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            n = data.get("strategy_name")
            if isinstance(n, str):
                s = n.strip()
                if s:
                    return s[:512]
    return ""


def _coding_agent_usage_limit_error(text: str) -> bool:
    s = (text or "").lower()
    if "usage limit" not in s:
        return False
    return (
        "try again" in s
        or "request to your admin" in s
        or "more access" in s
        or "hit your usage limit" in s
    )


@traceable(name="run_update_strategy", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def run_update_strategy(
    thread_id: str,
    task: str,
    on_progress: ProgressCallback = None,
    codex_thread_id: str | None = None,
) -> dict[str, Any]:
    task = (task or "").strip()
    if not task:
        return {"ok": False, "error": "task is empty"}
    if not thread_id_allowed(thread_id):
        return {"ok": False, "error": "invalid thread_id"}
    root = ensure_strategy_workspace(thread_id)
    ticker = (os.getenv("STRATEGY_BACKTEST_TICKER") or "SPY").strip() or "SPY"

    if on_progress:
        on_progress("Updating strategy, this may take a few minutes…")
    existing_codex_thread_id = _clean_codex_thread_id(codex_thread_id)
    runner, codegen = _run_coding_agent_exec(
        task,
        root,
        codex_thread_id=existing_codex_thread_id,
    )
    logger.info(f"Coding agent exec result: {runner}, {codegen.returncode}, {codegen.stdout[:100]}, {codegen.stderr[:100]}")
    next_codex_thread_id = existing_codex_thread_id
    if runner == "codex":
        next_codex_thread_id = _codex_thread_id_from_stdout(codegen.stdout or "") or existing_codex_thread_id
    result: dict[str, Any] = {
        "runner": runner,
        "codex_returncode": codegen.returncode,
        "codex_stdout": _tail(codegen.stdout or ""),
        "codex_stderr": _tail(codegen.stderr or ""),
        "codex_thread_id": next_codex_thread_id,
        "ok": codegen.returncode == 0,
    }
    if codegen.returncode != 0:
        combined_output = f"{codegen.stdout or ''}\n{codegen.stderr or ''}"
        if _coding_agent_usage_limit_error(combined_output):
            result["error"] = INTERNAL_LIMITS_MESSAGE
            result["terminal"] = True
        else:
            result["error"] = "claude code failed" if runner == "claude" else "codex exec failed"
    else:
        result["strategy_name"] = read_strategy_name_from_workspace(root)
    return result


def _read_params_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        data = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_strategy_description_from_workspace(root: Path) -> str:
    candidates = (
        root / "params.json",
    )
    for path in candidates:
        data = _read_params_json_object(path)
        if not data:
            continue
        desc = data.get("description")
        if isinstance(desc, str):
            s = desc.strip()
            if s:
                return s[:2048]
    return ""


def _deep_merge_json_values(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        out = dict(base)
        for key, value in overlay.items():
            if key in out:
                out[key] = _deep_merge_json_values(out[key], value)
            else:
                out[key] = value
        return out
    if isinstance(base, list) and isinstance(overlay, list):
        length = max(len(base), len(overlay))
        merged: list[Any] = []
        for i in range(length):
            if i >= len(base):
                merged.append(overlay[i])
            elif i >= len(overlay):
                merged.append(base[i])
            else:
                merged.append(_deep_merge_json_values(base[i], overlay[i]))
        return merged
    return overlay


def _merge_parameters_json_into_params_file(root: Path, parameters_json: Any) -> None:
    if parameters_json is None:
        return

    parsed: Any
    if isinstance(parameters_json, str):
        raw = parameters_json.strip()
        if not raw:
            return
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("parameters_json is not valid JSON")
    else:
        parsed = parameters_json

    root.mkdir(parents=True, exist_ok=True)
    params_path = root / "params.json"
    if isinstance(parsed, dict):
        existing = _read_params_json_object(params_path)
        to_write = _deep_merge_json_values(existing, parsed)
    else:
        to_write = parsed
    params_path.write_text(
        json.dumps(to_write, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _merge_parameters_hyperopt_json_into_params_hyperopt_file(
    root: Path, parameters_hyperopt_json: Any
) -> None:
    if parameters_hyperopt_json is None:
        return

    parsed: Any
    if isinstance(parameters_hyperopt_json, str):
        raw = parameters_hyperopt_json.strip()
        if not raw:
            return
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("parameters_hyperopt_json is not valid JSON")
    else:
        parsed = parameters_hyperopt_json

    if not isinstance(parsed, dict):
        raise ValueError("parameters_hyperopt_json must be a JSON object")

    try:
        overlay = ParamsHyperoptOverrides.model_validate(parsed).model_dump(
            mode="json", exclude_none=True
        )
    except ValidationError as e:
        raise ValueError(f"parameters_hyperopt_json does not match ParamsHyperopt: {e}") from e
    if not overlay:
        return

    root.mkdir(parents=True, exist_ok=True)
    path = root / "params-hyperopt.json"
    existing = _read_params_json_object(path)
    to_write = _deep_merge_json_values(existing, overlay)
    try:
        ParamsHyperopt.model_validate(to_write)
    except ValidationError as e:
        raise ValueError(f"merged params-hyperopt.json does not match ParamsHyperopt: {e}") from e
    path.write_text(
        json.dumps(to_write, indent=2, sort_keys=True),
        encoding="utf-8",
    )


_REDACT_JSON_KEYS_FOR_USER = frozenset(
    {
        "openrouter_api_key",
        "langsmith_api_key",
        "openai_api_key",
        "alpaca_api_key",
        "alpaca_secret_key",
        "postgres_password",
    }
)


def _is_secret_json_key(key: str) -> bool:
    n = (key or "").strip().lower().replace("-", "_")
    return n in _REDACT_JSON_KEYS_FOR_USER


def redact_secret_json_values_for_user(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            ks = str(k) if k is not None else ""
            if _is_secret_json_key(ks):
                out[k] = ""
            else:
                out[k] = redact_secret_json_values_for_user(v)
        return out
    if isinstance(obj, list):
        return [redact_secret_json_values_for_user(x) for x in obj]
    if isinstance(obj, str):
        stripped = obj.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(obj)
            except json.JSONDecodeError:
                return obj
            if isinstance(parsed, (dict, list)):
                redacted = redact_secret_json_values_for_user(parsed)
                return json.dumps(redacted, indent=2, sort_keys=True)
        return obj
    return obj


def _simulation_inputs_from_params(root: Path) -> tuple[str, str, float, str | None] | str:
    params = _read_params_json_object(root / "params.json")
    start_date = params.get("start_date")
    end_date = params.get("end_date")
    if not isinstance(start_date, str) or not start_date.strip():
        return "params.json is missing start_date (YYYY-MM-DD)"
    if not isinstance(end_date, str) or not end_date.strip():
        return "params.json is missing end_date (YYYY-MM-DD)"
    raw_deposit = params.get("initial_deposit", 10_000)
    try:
        deposit = float(raw_deposit)
    except (TypeError, ValueError):
        return "params.json initial_deposit must be a number"
    if deposit <= 0:
        return "params.json initial_deposit must be positive"
    provider_raw = params.get("provider")
    provider = provider_raw.strip() if isinstance(provider_raw, str) and provider_raw.strip() else None
    return start_date.strip(), end_date.strip(), deposit, provider


def _is_simulator_command(command: str) -> bool:
    parts = shlex.split(command or "")
    if not parts:
        return False
    if parts[-1] != "strategy.py":
        return False
    return len(parts) == 1 or parts[0] in ("python", "python3") or parts[0] == sys.executable


def _is_hyperopt_command(command: str) -> bool:
    parts = shlex.split(command or "")
    if not parts:
        return False
    if parts[-1] != "hyperopt.py":
        return False
    return len(parts) == 1 or parts[0] in ("python", "python3") or parts[0] == sys.executable


@traceable(name="run_backtest", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def run_backtest(
    thread_id: str,
    command: str,
    on_progress: ProgressCallback = None,
    parameters_json: Any = None,
    parameters_hyperopt_json: Any = None,
    codex_thread_id: str | None = None,
) -> dict[str, Any]:
    if not thread_id_allowed(thread_id):
        return {"ok": False, "error": "invalid thread_id"}
    command = (command or "").strip()
    if not command:
        return {"ok": False, "error": "command is empty"}
    root = ensure_strategy_workspace(thread_id)
    fixed_codex_thread_id = ""
    if parameters_json is not None:
        try:
            _merge_parameters_json_into_params_file(root, parameters_json)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    if parameters_hyperopt_json is not None and _is_hyperopt_command(command):
        try:
            _merge_parameters_hyperopt_json_into_params_hyperopt_file(
                root, parameters_hyperopt_json
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    if _is_simulator_command(command):
        resolved = _simulation_inputs_from_params(root)
        if isinstance(resolved, str):
            return {"ok": False, "error": resolved}
        start_date, end_date, deposit, provider = resolved
        if on_progress:
            on_progress("Running simulation…")
        def _on_sim_stderr_line(line: str) -> None:
            if not on_progress:
                return
            msg = _simulation_ui_line_to_status_text(line)
            if msg:
                on_progress(msg)
        try:
            bt = _run_logged_subprocess_with_freeze_watchdog(
                label="strategy simulation",
                cmd=[
                    sys.executable,
                    str(SIMULATE_SCRIPT_PATH),
                    "--entry",
                    str(root / "strategy.py"),
                ],
                cwd=str(root),
                timeout=int(os.getenv("STRATEGY_BACKTEST_TIMEOUT_S", "1800")),
                freeze_timeout=int(os.getenv("STRATEGY_BACKTEST_FREEZE_TIMEOUT_S", "120")),
                on_stderr_line=_on_sim_stderr_line if on_progress else None,
            )
        except subprocess.TimeoutExpired as e:
            freeze_timeout_s = int(os.getenv("STRATEGY_BACKTEST_FREEZE_TIMEOUT_S", "120"))
            msg = f"backtest froze and was killed after {freeze_timeout_s}s without output"
            logger.error("%s: %s", "strategy simulation", msg)

            fix_attempt = run_update_strategy(
                thread_id,
                (
                    "The backtest froze and was killed due to no output for over 120 seconds. "
                    "Fix strategy.py so it cannot hang: remove infinite loops, ensure per-bar processing is fast, "
                    "avoid blocking network calls, and always read stdin line-by-line and emit outputs regularly. "
                    "Then keep the strategy logic intact as much as possible."
                ),
                on_progress=on_progress,
                codex_thread_id=codex_thread_id,
            )
            fixed_codex_thread_id = _clean_codex_thread_id(str(fix_attempt.get("codex_thread_id") or ""))
            if fix_attempt.get("ok"):
                if on_progress:
                    on_progress("Retrying simulation after auto-fix…")
                bt = _run_logged_subprocess_with_freeze_watchdog(
                    label="strategy simulation",
                    cmd=[
                        sys.executable,
                        str(SIMULATE_SCRIPT_PATH),
                        "--entry",
                        str(root / "strategy.py"),
                    ],
                    cwd=str(root),
                    timeout=int(os.getenv("STRATEGY_BACKTEST_TIMEOUT_S", "1800")),
                    freeze_timeout=int(os.getenv("STRATEGY_BACKTEST_FREEZE_TIMEOUT_S", "180")),
                    on_stderr_line=_on_sim_stderr_line if on_progress else None,
                )
            else:
                return {
                    "ok": False,
                    "error": msg,
                    "backtest_returncode": 124,
                    "backtest_stdout": "",
                    "backtest_stderr": "",
                    "codex_thread_id": fixed_codex_thread_id,
                    "autofix": fix_attempt,
                }
        extras: dict[str, Any] = {
            "start_date": start_date,
            "end_date": end_date,
            "initial_deposit": deposit,
            "provider": provider or "auto",
        }
        failure_message = "simulation failed"
    else:
        if on_progress:
            on_progress("Optimizing strategy parameters…")

        def _on_hyperopt_stderr_line(line: str) -> None:
            if not on_progress:
                return
            msg = _hyperopt_ui_line_to_status_text(line)
            if msg:
                on_progress(msg)

        bt = _run_workspace_command(
            command,
            root,
            on_stderr_line=_on_hyperopt_stderr_line if on_progress else None,
        )
        extras = {}
        failure_message = "hyperopt failed"

    result: dict[str, Any] = {
        "command": command,
        **extras,
        "backtest_returncode": bt.returncode,
        "backtest_stdout": _tail(bt.stdout or ""),
        "backtest_stderr": _tail(bt.stderr or ""),
        "ok": bt.returncode == 0,
    }
    if bt.returncode != 0:
        result["error"] = failure_message
    if fixed_codex_thread_id:
        result["codex_thread_id"] = fixed_codex_thread_id
    return result


def _tool_handlers_for_thread(
    thread_id: str,
    *,
    on_progress: ProgressCallback = None,
    codex_thread_ref: dict[str, str] | None = None,
) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    def _remember_codex_thread_id(payload: dict[str, Any]) -> None:
        if codex_thread_ref is None:
            return
        tid = _clean_codex_thread_id(str(payload.get("codex_thread_id") or ""))
        if tid:
            codex_thread_ref["value"] = tid

    def _update(args: dict[str, Any]) -> dict[str, Any]:
        payload = run_update_strategy(
            thread_id,
            str(args.get("task", "")),
            on_progress=on_progress,
            codex_thread_id=(codex_thread_ref or {}).get("value", ""),
        )
        _remember_codex_thread_id(payload)
        return payload

    def _run_backtest_tool(args: dict[str, Any]) -> dict[str, Any]:
        payload = run_backtest(
            thread_id,
            "python strategy.py",
            on_progress=on_progress,
            parameters_json=args.get("parameters_json"),
            parameters_hyperopt_json=None,
            codex_thread_id=(codex_thread_ref or {}).get("value", ""),
        )
        _remember_codex_thread_id(payload)
        return payload

    def _run_hyperopt_tool(args: dict[str, Any]) -> dict[str, Any]:
        payload = run_backtest(
            thread_id,
            "python hyperopt.py",
            on_progress=on_progress,
            parameters_json=args.get("parameters_json"),
            parameters_hyperopt_json=args.get("parameters_hyperopt_json"),
            codex_thread_id=(codex_thread_ref or {}).get("value", ""),
        )
        _remember_codex_thread_id(payload)
        return payload

    def _analyse(args: dict[str, Any]) -> dict[str, Any]:
        return run_analyse_code(
            thread_id=thread_id,
            question=str(args.get("question", "")),
        )

    def _analyse_run(args: dict[str, Any]) -> dict[str, Any]:
        payload = run_analyse_run(
            thread_id=thread_id,
            question=str(args.get("question", "")),
            on_progress=on_progress,
            codex_thread_id=(codex_thread_ref or {}).get("value", ""),
        )
        _remember_codex_thread_id(payload)
        return payload

    def _list_tickers(args: dict[str, Any]) -> dict[str, Any]:
        return run_list_tickers(
            query=str(args.get("query", "")),
            limit=args.get("limit"),
        )

    return {
        LIST_TICKERS_TOOL_NAME: _list_tickers,
        UPDATE_STRATEGY_TOOL_NAME: _update,
        RUN_BACKTEST_TOOL_NAME: _run_backtest_tool,
        RUN_HYPEROPT_TOOL_NAME: _run_hyperopt_tool,
        ANALYSE_RUN_TOOL_NAME: _analyse_run,
        ANALYSE_CODE_TOOL_NAME: _analyse,
    }


def _stored_messages_to_lc(messages: list[dict[str, Any]]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for m in messages:
        role = str(m.get("role", ""))
        raw = m.get("content", "")
        if raw is None:
            content = ""
        elif isinstance(raw, str):
            content = raw
        else:
            content = str(raw)
        if role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
    return out


def _strategy_help_for_workspace(workspace: Path) -> str:
    params_path = workspace / "params.json"
    strategy_parameters = ""
    if params_path.is_file():
        try:
            strategy_parameters = params_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            strategy_parameters = ""
    hyperopt_path = workspace / "params-hyperopt.json"
    hyperopt_parameters = ""
    if hyperopt_path.is_file():
        try:
            hyperopt_parameters = hyperopt_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            hyperopt_parameters = ""
    metrics_path = workspace / "metrics.json"
    metrics_text = ""
    if metrics_path.is_file():
        try:
            metrics_text = metrics_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            metrics_text = ""
    trained_path = workspace / "trained_model_params.json"
    trained_text = ""
    if trained_path.is_file():
        try:
            trained_text = trained_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            trained_text = ""
    hyperopt_section = (
        f"\nThe current strategy supports hyperparameter optimization. The params-hyperopt.json file contains the optimization configuration:\n{hyperopt_parameters}\n"
        if hyperopt_parameters
        else ""
    )
    metrics_section = (
        f"\nLatest metrics from metrics.json:\n{metrics_text}\n"
        if metrics_text
        else ""
    )
    trained_section = (
        f"\nThe current strategy has trained model parameters in trained_model_params.json. Test-mode trainable strategies receive these as an initial trained_model_params input object:\n{trained_text}\n"
        if trained_text
        else ""
    )
    if params_path.is_file():
        return f"""Strategy inputs are read from params.json (overrides: pass parameters_json on run_backtest, or on run_hyperopt only when the current user explicitly asks to optimize parameters). On run_hyperopt, optional parameters_hyperopt_json merges into params-hyperopt.json (which params are optimised, ranges, regimes, study budget—not ticker/dates from params.json).
{strategy_parameters}
{hyperopt_section}{trained_section}{metrics_section}"""
    return f"""Note: params.json hasn't been created yet. Need to run update_strategy first.

Current params.json (may be empty or missing):
{strategy_parameters}
On run_hyperopt, only when the current user explicitly asks to optimize parameters, optional parameters_hyperopt_json merges into params-hyperopt.json (study definition: optimised params, ranges, regimes—not params.json backtest inputs).
{hyperopt_section}{trained_section}{metrics_section}"""


def _aimessage_plain_text(msg: AIMessage) -> str:
    c = msg.content
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: list[str] = []
        for block in c:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(c)


async def _chat_openrouter_ainvoke_with_timeout_retries(
    llm: Any,
    chat_messages: list[BaseMessage],
    *,
    timeout_seconds: float = CHAT_OPENROUTER_AINVOKE_TIMEOUT_SECONDS,
    retries: int = CHAT_OPENROUTER_AINVOKE_TIMEOUT_RETRIES,
) -> AIMessage:
    last_timeout: TimeoutError | None = None
    for retry in range(retries + 1):
        try:
            msg = await asyncio.wait_for(llm.ainvoke(chat_messages), timeout=timeout_seconds)
            if isinstance(msg, AIMessage):
                return msg
            return AIMessage(content=getattr(msg, "content", ""))
        except asyncio.TimeoutError as e:
            last_timeout = e
            if retry >= retries:
                raise
            logger.warning(
                "ChatOpenRouter ainvoke timed out; retrying",
                extra={
                    "attempt": retry + 1,
                    "max_retries": retries,
                    "timeout_seconds": timeout_seconds,
                },
            )
    raise last_timeout or TimeoutError("ChatOpenRouter ainvoke timed out")


def _run_chat_openrouter_ainvoke(
    llm: Any,
    chat_messages: list[BaseMessage],
    *,
    timeout_seconds: float = CHAT_OPENROUTER_AINVOKE_TIMEOUT_SECONDS,
    retries: int = CHAT_OPENROUTER_AINVOKE_TIMEOUT_RETRIES,
) -> AIMessage:
    return asyncio.run(
        _chat_openrouter_ainvoke_with_timeout_retries(
            llm,
            chat_messages,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
    )


def _tool_call_parts(tc: Any) -> tuple[str, dict[str, Any], str]:
    if isinstance(tc, dict):
        name = str(tc.get("name", "") or "")
        tid = str(tc.get("id", "") or "")
        args = tc.get("args")
        if args is None and "function" in tc:
            fn = tc.get("function") or {}
            raw = fn.get("arguments") if isinstance(fn, dict) else None
            if isinstance(raw, str):
                try:
                    args = json.loads(raw or "{}")
                except json.JSONDecodeError:
                    args = {}
            else:
                args = raw if isinstance(raw, dict) else {}
        if not isinstance(args, dict):
            args = {}
        return name, args, tid
    name = str(getattr(tc, "name", "") or "")
    tid = str(getattr(tc, "id", "") or "")
    args = getattr(tc, "args", None)
    if not isinstance(args, dict):
        args = {}
    return name, args, tid


def _strip_reasoning_details(msg: AIMessage) -> AIMessage:
    if not msg.additional_kwargs.get("reasoning_details"):
        return msg
    new_kwargs = {k: v for k, v in msg.additional_kwargs.items() if k != "reasoning_details"}
    return AIMessage(
        content=msg.content,
        tool_calls=msg.tool_calls,
        additional_kwargs=new_kwargs,
        response_metadata=msg.response_metadata,
        id=msg.id,
    )


def _invoke_agent_model(llm_tools: Any, chat_messages: list[BaseMessage], on_token: TokenCallback) -> AIMessage:
    msg = _run_chat_openrouter_ainvoke(llm_tools, chat_messages)
    if on_token is not None and not msg.tool_calls:
        content = _aimessage_plain_text(msg)
        if content:
            on_token(content)
    return msg


@traceable(name="build_agent_reply", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def build_agent_reply(
    messages: list[dict[str, Any]],
    existing_canvas: dict[str, Any],
    thread_id: str,
    on_progress: ProgressCallback = None,
    on_token: TokenCallback = None,
    codex_thread_id: str | None = None,
    user_timezone: str = "",
) -> dict[str, Any]:
    t0 = time.perf_counter()

    def _reply_duration_ms() -> int:
        return int(round((time.perf_counter() - t0) * 1000))

    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        return {
            "message": (
                "OPENROUTER_API_KEY is not configured. Your message was saved. "
                "Set the key to enable live agent responses."
            ),
            "canvas": canvas_with_output(existing_canvas, thread_id),
            "reply_duration_ms": _reply_duration_ms(),
            "strategy_name": "",
            "codex_thread_id": _clean_codex_thread_id(codex_thread_id),
        }

    workspace = strategy_root_for_thread(thread_id)
    strategy_help = _strategy_help_for_workspace(workspace)
    user_timezone_line = (
        f"\n* The user's local timezone is {user_timezone}. When the user mentions a clock time (e.g. '9:30am'), interpret it in this timezone and convert to UTC for strategy code unless the user explicitly specifies a different timezone. Strategy unixtime values are UTC-based POSIX seconds."
        if user_timezone
        else ""
    )
    chat_messages: list[BaseMessage] = [
        SystemMessage(content=SYSTEM_PROMPT.format(strategy_help=strategy_help, user_timezone_line=user_timezone_line)),
        *_stored_messages_to_lc(messages),
    ]

    max_iterations = 10
    last_strategy_name = ""
    codex_thread_ref = {"value": _clean_codex_thread_id(codex_thread_id)}
    tool_handlers = _tool_handlers_for_thread(
        thread_id,
        on_progress=on_progress,
        codex_thread_ref=codex_thread_ref,
    )

    for _ in range(max_iterations):
        chat_messages[0] = SystemMessage(
            content=SYSTEM_PROMPT.format(
                strategy_help=_strategy_help_for_workspace(workspace),
                user_timezone_line=user_timezone_line,
            )
        )
        if on_progress:
            on_progress("Thinking…")
        llm = ChatOpenRouter(
            model=CHAT_MODEL,
            request_timeout=120_000,
            reasoning={"effort": CHAT_REASONING_EFFORT},
            openrouter_provider=OPENROUTER_PROVIDER,
        )
        llm_tools = llm.bind_tools(AGENT_TOOLS)
        assistant_msg = _invoke_agent_model(llm_tools, chat_messages, on_token)
        chat_messages.append(_strip_reasoning_details(assistant_msg))
        tool_calls = assistant_msg.tool_calls or []
        if not tool_calls:
            content = _aimessage_plain_text(assistant_msg).strip()
            if not content:
                raise Exception(
                    "The model returned an empty message. Try again or adjust CHAT_MODEL; "
                    "empty content can happen when a provider blocks the request or returns no completion."
                )
            return {
                "message": content,
                "canvas": canvas_with_output(existing_canvas, thread_id),
                "reply_duration_ms": _reply_duration_ms(),
                "strategy_name": last_strategy_name,
                "codex_thread_id": codex_thread_ref["value"],
            }
        for tc in tool_calls:
            name, parsed_args, tid = _tool_call_parts(tc)
            handler = tool_handlers.get(name)
            if handler is None:
                tool_payload: dict[str, Any] = {"ok": False, "error": f"unknown tool: {name}"}
            else:
                try:
                    tool_payload = handler(parsed_args)
                except Exception as e:
                    logger.exception(
                        "tool execution failed",
                        extra={
                            "thread_id": thread_id,
                            "tool_name": name,
                            "tool_call_id": tid,
                            "tool_args": parsed_args,
                        },
                    )
                    tool_payload = {"ok": False, "error": f"tool execution failed: {type(e).__name__}: {e}"}
            if isinstance(tool_payload, dict) and tool_payload.get("terminal"):
                return {
                    "message": str(tool_payload.get("error") or INTERNAL_LIMITS_MESSAGE),
                    "canvas": canvas_with_output(existing_canvas, thread_id),
                    "reply_duration_ms": _reply_duration_ms(),
                    "strategy_name": last_strategy_name,
                    "codex_thread_id": codex_thread_ref["value"],
                }
            if (
                name == UPDATE_STRATEGY_TOOL_NAME
                and isinstance(tool_payload, dict)
                and tool_payload.get("ok")
            ):
                sn = tool_payload.get("strategy_name")
                if isinstance(sn, str) and sn.strip():
                    last_strategy_name = sn.strip()[:512]
            limited = tool_payload
            if name == UPDATE_STRATEGY_TOOL_NAME:
                limited = _trim_tool_payload_streams(
                    tool_payload,
                    UPDATE_STRATEGY_TOOL_MESSAGE_MAX_JSON,
                    "codex_stdout",
                    "codex_stderr",
                )
            elif name in (RUN_BACKTEST_TOOL_NAME, RUN_HYPEROPT_TOOL_NAME):
                limited = _trim_tool_payload_streams(
                    tool_payload,
                    RUN_EXECUTION_TOOL_MESSAGE_MAX_JSON,
                    "backtest_stdout",
                    "backtest_stderr",
                )
            elif name == ANALYSE_RUN_TOOL_NAME:
                limited = _trim_tool_payload_streams(
                    tool_payload,
                    ANALYSE_RUN_TOOL_MESSAGE_MAX_JSON,
                    "codex_stdout",
                    "codex_stderr",
                )
            chat_messages.append(
                ToolMessage(content=json.dumps(limited), tool_call_id=tid)
            )

    raise Exception("Agent stopped: maximum tool iterations reached without a final reply.")
