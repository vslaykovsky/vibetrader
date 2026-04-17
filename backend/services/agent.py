from __future__ import annotations
from datetime import datetime, timedelta
import dotenv
dotenv.load_dotenv()
from langsmith import traceable
import json
import logging
import orjson
import os
import re
import shutil
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from langchain_openrouter import ChatOpenRouter


CHAT_MODEL = 'openai/gpt-5.4-mini'
CHAT_REASONING_EFFORT = 'low'

CODEX_MODEL = 'gpt-5.4-mini'
CODEX_REASONING_EFFORT = 'low'

CODE_ANALYSIS_MODEL = 'anthropic/claude-opus-4.7'

SYSTEM_PROMPT = f"""You help users design trading strategies in chat.

Workflow

* Before the first update_strategy, request any missing details needed to build the strategy (e.g., ticker, candlestick period, time range, stop loss, take profit, other parameters). 
* Do not add hyperparameter search loops inside strategy.py by default; optimization is driven by the fixed workspace script hyperopt.py when the user asks for it.
* To modify code call update_strategy with a brief task describing only the changes. Use english for the task description.
* If the user only tweaks existing parameters (different ticker, dates, thresholds, etc.), call run_strategy with `python strategy.py` instead of update_strategy; pass parameters_json as a JSON string so the tool recursively merges into params.json before the run (do not use a --params CLI flag or teach strategies to accept one). 
* If the user asks for exploratory data analysis, market research, or charts **without** defining a tradable strategy (no signals, no rules backtest), use `update_strategy` for the analysis path, then run_strategy with `python strategy.py`. That path must not write metrics.json or params-hyperopt.json.
* If the user asks for training/hyperparameter optimization, ensure via update_strategy that the strategy workspace writes params-hyperopt.json on runs (and metrics.json as a strategy), then run_strategy with `python hyperopt.py` (not strategy.py).
* Always respond in the user’s language.
* After each successful update_strategy, call run_strategy so results match the change (`python strategy.py` or `python hyperopt.py` as appropriate). Only briefly summarize strategy performance based on the output of the run_strategy call, don't make up numbers. The user already sees all the charts and metrics.

Notes
* update_strategy edits the workspace strategy code via the coding agent; layout, run contract, and how results are surfaced follow AGENTS.md in that workspace.
* Market data providers: use Alpaca for all non-Russian markets; use MOEX (moexalgo/Algopack) for Russian instruments/markets.
* Auto provider selection is allowed and preferred when uncertain: try Alpaca first, then MOEX.
* Do not use yfinance or ask the user to switch to yfinance.
* Backtesting is supported; live trading is not.
* Today's date is {(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")}.

{{strategy_help}}

Answer in plain text. No JSON or markup unless the user asks."""

STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "strategies"
STRATEGY_AGENTS_TEMPLATE = STRATEGIES_DIR / "AGENTS.md"
STRATEGY_CLAUDE_TEMPLATE = STRATEGIES_DIR / "CLAUDE.md"
STRATEGY_CODE_TEMPLATE = STRATEGIES_DIR / "strategy.py"
STRATEGY_UTILS_TEMPLATE = STRATEGIES_DIR / "utils.py"
STRATEGY_HYPEROPT_TEMPLATE = STRATEGIES_DIR / "hyperopt.py"
STRATEGY_CODE_AGENT_PREFIX = ""
UPDATE_STRATEGY_TOOL_NAME = "update_strategy"
RUN_STRATEGY_TOOL_NAME = "run_strategy"
UPDATE_STRATEGY_TOOL_MESSAGE_MAX_JSON = 1024
RUN_STRATEGY_TOOL_MESSAGE_MAX_JSON = 4096
ANALYSE_CODE_TOOL_NAME = "analyse_code"

ProgressCallback = Callable[[str], None] | None

CODING_TOOL = 'codex'

logger = logging.getLogger(__name__)


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
    dest_claude = workspace / "CLAUDE.md"
    if not dest_claude.is_file() and STRATEGY_CLAUDE_TEMPLATE.is_file():
        shutil.copy2(STRATEGY_CLAUDE_TEMPLATE, dest_claude)
    dest_strategy = workspace / "strategy.py"
    legacy_strategy = workspace / "src" / "strategy.py"
    if not dest_strategy.is_file():
        if legacy_strategy.is_file():
            shutil.copy2(legacy_strategy, dest_strategy)
        elif STRATEGY_CODE_TEMPLATE.is_file():
            shutil.copy2(STRATEGY_CODE_TEMPLATE, dest_strategy)
    dest_utils = workspace / "utils.py"
    legacy_utils = workspace / "src" / "utils.py"
    if not dest_utils.is_file():
        if legacy_utils.is_file():
            shutil.copy2(legacy_utils, dest_utils)
        elif STRATEGY_UTILS_TEMPLATE.is_file():
            shutil.copy2(STRATEGY_UTILS_TEMPLATE, dest_utils)
    if dest_utils.is_file():
        _chmod_readonly(dest_utils)
    dest_hyperopt = workspace / "hyperopt.py"
    if STRATEGY_HYPEROPT_TEMPLATE.is_file():
        if dest_hyperopt.is_file():
            try:
                dest_hyperopt.chmod(0o644)
            except OSError:
                logger.warning("could not make writable for refresh: %s", dest_hyperopt)
        shutil.copy2(STRATEGY_HYPEROPT_TEMPLATE, dest_hyperopt)
        _chmod_readonly(dest_hyperopt)
    return workspace


def strategy_root_for_thread(thread_id: str) -> Path:
    return ensure_strategy_workspace(thread_id)


def read_strategy_code(thread_id: str) -> str:
    root = ensure_strategy_workspace(thread_id)
    path = root / "strategy.py"
    if not path.is_file():
        path = root / "src" / "strategy.py"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def read_strategy_utils(thread_id: str) -> str:
    root = ensure_strategy_workspace(thread_id)
    path = root / "utils.py"
    if not path.is_file():
        path = root / "src" / "utils.py"
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

    legacy_output_dir = root / "output"
    shutil.rmtree(legacy_output_dir, ignore_errors=True)
    for path in (
        root / "params.json",
        root / "backtest.json",
        root / "metrics.json",
        root / "params-hyperopt.json",
        root / "data.json",
    ):
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

    allowed = frozenset(
        {
            "params.json",
            "backtest.json",
            "metrics.json",
            "params-hyperopt.json",
            "data.json",
        }
    )
    for filename, contents in output.items():
        if not isinstance(filename, str) or not filename:
            continue
        name = filename.strip()
        if name.lower() not in allowed:
            continue
        if name.lower() == "data.json":
            name = "backtest.json"
        out_path = root / name
        if out_path.name != name:
            continue
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
            "name": UPDATE_STRATEGY_TOOL_NAME,
            "description": (
                "Implement strategy or analysis changes in this thread's strategy workspace using the configured coding agent. "
                "Workspace conventions are in AGENTS.md there. After it succeeds, call run_strategy to refresh outputs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "High-level goal and required behavior or code changes only; use english for the task description."
                            "Do not specify any file paths or filenames, the tool already known them"
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
            "name": RUN_STRATEGY_TOOL_NAME,
            "description": (
                "Run a strategy command in this thread's workspace (no coding agent, no code edits). "
                "Use python strategy.py for all normal runs (single entrypoint; params come from params.json). "
                "Optional parameters_json (a JSON string) is parsed and recursively merged into params.json before the command runs (same merge rules as parameters_json field description). "
                "Use python hyperopt.py when the user asked for hyperparameter optimization and the strategy writes params-hyperopt.json and metrics.json. "
                "Refreshes backtest.json on success."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "Full shell command, e.g. python strategy.py or python hyperopt.py. "
                            "Pass ticker or other overrides in parameters_json (merged into params.json), not on the command line."
                        ),
                    },
                    "parameters_json": {
                        "type": "string",
                        "description": (
                            "If provided, must be valid JSON. Object values are merged recursively into existing params.json "
                            "(nested dicts merged by key, lists merged by index with recursive dict merge where both sides are objects; "
                            "scalars and mismatched types take the new value). Non-object JSON replaces the file."
                        ),
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ANALYSE_CODE_TOOL_NAME,
            "description": (
                "Answer a question about the current strategy's code (strategy.py, utils.py, hyperopt.py), "
                "and params in this thread. "
                "Use for quick code comprehension without modifying files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "A natural-language question about the current strategy code.",
                    }
                },
                "required": ["question"],
            },
        },
    },
]


def _tail(s: str, max_chars: int = 12_000) -> str:
    if len(s) <= max_chars:
        return s
    return s[-max_chars:]


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


@traceable(name="run_analyse_code")
def run_analyse_code(
    *,
    thread_id: str,
    question: str,
    model: str,
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
        "You are a code analyst for a trading strategy project. "
        "Answer the user's question using ONLY the provided strategy code, utils.py, and params. "
        "Be concise: 1-4 sentences. If the answer cannot be determined, say what is missing."
    )
    context = (
        "Strategy params (JSON, may be empty):\n"
        f"{params_text if params_text else ''}\n\n"
        "utils.py (Python, may be empty):\n"
        f"{utils_text if utils_text else ''}\n\n"
        "strategy.py (Python, may be empty):\n"
        f"{code if code else ''}"
    )

    llm = ChatOpenRouter(model=CODE_ANALYSIS_MODEL, request_timeout=120_000)
    msg = llm.invoke(
        [
            SystemMessage(content=analysis_system),
            SystemMessage(content=context),
            HumanMessage(content=q),
        ]
    )
    answer = _aimessage_plain_text(msg).strip()
    return {"ok": True, "answer": answer}


@traceable(name="generate_strategy_algorithm_pseudocode")
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
        f"The user's conversation language (ISO 639-1) is {lang}. Write all natural-language explanations, "
        f"step titles, and comments in that language."
        if lang
        else "The user's conversation language is unknown; write all natural-language explanations, step titles, and comments in English."
    )
    system = (
        "Write very compact, language-agnostic pseudocode for the core business logic of this trading strategy only. "
        "Omit boilerplate entirely: CLI/argparse, imports, logging, file I/O, HTTP/API calls, "
        "dataframe plumbing, plotting, JSON/chart serialization, and generic helpers unless they directly encode a trading rule. "
        "Do NOT include or rewrite the pseudocode of internal logic for standard indicators (like RSI, MACD, etc) — if strategy uses a standard indicator, mention its use without describing or outlining its internal computation. "
        "No long code quotes. Prefer tight numbered steps or compact bullets. If something essential is ambiguous in the source, "
        "state the single most likely interpretation in one line. "
        "Can use Markdown for formatting. "
        + lang_line
    )
    llm = ChatOpenRouter(model=CODE_ANALYSIS_MODEL, request_timeout=120_000)
    msg = llm.invoke(
        [
            SystemMessage(content=system),
            HumanMessage(
                content="Extract core-algorithm pseudocode from this strategy.py (business logic only, no I/O boilerplate).\n\n"
                + src
            ),
        ]
    )
    text = _aimessage_plain_text(msg).strip()
    return {"ok": True, "algorithm": text or "(empty response)"}


def _strategy_output_file_key(filename: str) -> str:
    lower = filename.lower()
    if lower == "data.json":
        return "backtest.json"
    return filename


_IGNORED_STRATEGY_OUTPUT_FILES = frozenset(
    {
        "summary.txt",
        "pseudocode.txt",
        "pseudocode.diff",
        "pseudocode.old",
    }
)


def _read_strategy_output_dir(thread_id: str) -> dict[str, Any]:
    root = strategy_root_for_thread(thread_id)
    legacy_output_dir = root / "output"
    out: dict[str, Any] = {}
    filenames = ("params.json", "backtest.json", "data.json", "metrics.json", "params-hyperopt.json")
    candidates: list[Path] = []
    for name in filenames:
        candidates.append(root / name)
        candidates.append(legacy_output_dir / name)
    for path in candidates:
        if not path.is_file():
            continue
        if path.name.lower() in _IGNORED_STRATEGY_OUTPUT_FILES:
            continue
        key = _strategy_output_file_key(path.name)
        raw = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() == ".json":
            try:
                out[key] = json.loads(raw)
            except json.JSONDecodeError:
                out[key] = raw
        else:
            out[key] = raw
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
        disk_output = _read_strategy_output_dir(thread_id)
        merged["output"] = {**existing_output, **disk_output} if disk_output else dict(existing_output)
        workspace = strategy_root_for_thread(thread_id)
        desc = _parse_argparse_help_description(_run_strategy_help_stdout(workspace))
        out = merged["output"]
        if isinstance(out, dict):
            if desc:
                out["strategy_cli_description"] = desc
            else:
                out.pop("strategy_cli_description", None)
    else:
        merged["output"] = {}
    return sanitize_json_for_postgres(merged)


@traceable(name="run_codex_exec")
def _run_codex_exec(task: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    cmd = [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "-c", "service_tier=fast",
        "-c", f"model={CODEX_MODEL}",
        "-c", "model_verbosity=low",
        "-c", f"model_reasoning_effort={CODEX_REASONING_EFFORT}", # minimal, low, medium, high, xhigh
        "-c", "features.fast_mode=true",
        task,
    ]
    return _run_logged_subprocess("codex exec", cmd, str(cwd), timeout=600)


@traceable(name="run_claude_exec")
def _run_claude_exec(task: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    cmd: list[str] = ['claude', '--output-format', 'json', '--permission-mode', 'bypassPermissions']
    cmd.extend(["-p", task ])
    return _run_logged_subprocess("claude", cmd, str(cwd), timeout=600)


def _run_coding_agent_exec(task: str, cwd: Path) -> tuple[str, subprocess.CompletedProcess[str]]:
    tool = CODING_TOOL    
    if tool == "claude":
        return tool, _run_claude_exec(task, cwd)
    return tool, _run_codex_exec(task, cwd)


@traceable(name="run_strategy")
def _run_strategy(
    command: str,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    parts = shlex.split(command)
    if parts and parts[0] == "python":
        parts[0] = sys.executable
    timeout_s = int(os.getenv("STRATEGY_BACKTEST_TIMEOUT_S", "1800"))
    return _run_logged_subprocess("strategy backtest", parts, str(cwd), timeout=timeout_s)


_ARGPARSE_SECTION_HEADER = re.compile(
    r"^(optional arguments|options|positional arguments|arguments)\s*:\s*$",
    re.IGNORECASE,
)


def _run_strategy_help_stdout(workspace: Path) -> str:
    rel: str | None = None
    if (workspace / "strategy.py").is_file():
        rel = "strategy.py"
    elif (workspace / "src" / "strategy.py").is_file():
        rel = "src/strategy.py"
    if not rel:
        return ""
    try:
        proc = subprocess.run(
            [sys.executable, rel, "--help"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").replace("\r\n", "\n").strip()


def _parse_argparse_help_description(help_text: str) -> str:
    text = (help_text or "").replace("\r\n", "\n").strip()
    if not text:
        return ""
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n and not lines[i].strip():
        i += 1
    if i >= n:
        return ""
    if lines[i].lower().startswith("usage:"):
        i += 1
        while i < n and (not lines[i].strip() or lines[i][:1] in (" ", "\t")):
            i += 1
    while i < n and not lines[i].strip():
        i += 1
    desc_lines: list[str] = []
    while i < n:
        stripped = lines[i].strip()
        if _ARGPARSE_SECTION_HEADER.match(stripped):
            break
        desc_lines.append(lines[i])
        i += 1
    return "\n".join(desc_lines).strip()


def _strategy_script_help(workspace: Path) -> str:
    return _tail(_run_strategy_help_stdout(workspace))


def read_strategy_name_from_workspace(root: Path) -> str:
    candidates = (
        root / "params.json",
        root / "backtest.json",
        root / "data.json",
        root / "output" / "params.json",
        root / "output" / "backtest.json",
        root / "output" / "data.json",
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


@traceable(name="run_update_strategy")
def run_update_strategy(
    thread_id: str, task: str, on_progress: ProgressCallback = None
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
    runner, codegen = _run_coding_agent_exec(STRATEGY_CODE_AGENT_PREFIX + task, root)
    logger.info(f"Coding agent exec result: {runner}, {codegen.returncode}, {codegen.stdout[:100]}, {codegen.stderr[:100]}")
    result: dict[str, Any] = {
        "runner": runner,
        "codex_returncode": codegen.returncode,
        "codex_stdout": _tail(codegen.stdout or ""),
        "codex_stderr": _tail(codegen.stderr or ""),
        "ok": codegen.returncode == 0,
    }
    if codegen.returncode != 0:
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


_REDACT_JSON_KEYS_FOR_USER = frozenset(
    {
        "openrouter_api_key",
        "langsmith_api_key",
        "openai_api_key",
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


@traceable(name="run_rerun_backtest")
def run_rerun_backtest(
    thread_id: str,
    command: str,
    on_progress: ProgressCallback = None,
    parameters_json: str | None = None,
) -> dict[str, Any]:
    if not thread_id_allowed(thread_id):
        return {"ok": False, "error": "invalid thread_id"}
    command = (command or "").strip()
    if not command:
        return {"ok": False, "error": "command is empty"}
    root = ensure_strategy_workspace(thread_id)
    if parameters_json is not None:
        raw = parameters_json.strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {"ok": False, "error": "parameters_json is not valid JSON"}
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
    if on_progress:
        on_progress("Running backtest…")
    bt = _run_strategy(command, root)
    result: dict[str, Any] = {
        "command": command,
        "backtest_returncode": bt.returncode,
        "backtest_stdout": _tail(bt.stdout or ""),
        "backtest_stderr": _tail(bt.stderr or ""),
        "ok": bt.returncode == 0,
    }
    if bt.returncode != 0:
        result["error"] = "backtest failed"
    return result


def _tool_handlers_for_thread(
    thread_id: str,
    *,
    model: str,
    on_progress: ProgressCallback = None,
) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    def _update(args: dict[str, Any]) -> dict[str, Any]:
        return run_update_strategy(thread_id, str(args.get("task", "")), on_progress=on_progress)

    def _rerun(args: dict[str, Any]) -> dict[str, Any]:
        command = str(args.get("command", ""))
        raw_params = args.get("parameters_json")
        parameters_json = raw_params if isinstance(raw_params, str) else None
        return run_rerun_backtest(
            thread_id, command, on_progress=on_progress, parameters_json=parameters_json
        )

    def _analyse(args: dict[str, Any]) -> dict[str, Any]:
        return run_analyse_code(
            thread_id=thread_id,
            question=str(args.get("question", "")),
            model=model,
        )

    return {
        UPDATE_STRATEGY_TOOL_NAME: _update,
        RUN_STRATEGY_TOOL_NAME: _rerun,
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

@traceable(name="build_agent_reply")
def build_agent_reply(
    model: str,
    messages: list[dict[str, Any]],
    existing_canvas: dict[str, Any],
    thread_id: str,
    on_progress: ProgressCallback = None,
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
        }

    workspace = strategy_root_for_thread(thread_id)
    strategy_help = _strategy_script_help(workspace)
    if strategy_help:
        strategy_parameters = ""
        params_path = workspace / "params.json"
        if params_path.is_file():
            with open(params_path, "r", encoding="utf-8") as f:
                strategy_parameters = f.read()
        strategy_help = f"""Help message of the current strategy script:
python strategy.py --help
{strategy_help}

Current strategy parameters (overrides: pass parameters_json on run_strategy to merge into this file):
{strategy_parameters}
"""
    else:
        strategy_help = "Note that script strategy.py hasn't been generated yet. Need to run update_strategy first."
    chat_messages: list[BaseMessage] = [
        SystemMessage(content=SYSTEM_PROMPT.format(strategy_help=strategy_help)),
        *_stored_messages_to_lc(messages),
    ]

    max_iterations = 10
    last_strategy_name = ""
    llm = ChatOpenRouter(model=CHAT_MODEL, request_timeout=120_000, reasoning={"effort": CHAT_REASONING_EFFORT})
    llm_tools = llm.bind_tools(AGENT_TOOLS)
    tool_handlers = _tool_handlers_for_thread(
        thread_id,
        model=model,
        on_progress=on_progress,
    )

    for _ in range(max_iterations):
        if on_progress:
            on_progress("Thinking…")
        assistant_msg = llm_tools.invoke(chat_messages)
        chat_messages.append(assistant_msg)
        tool_calls = assistant_msg.tool_calls or []
        if not tool_calls:
            content = _aimessage_plain_text(assistant_msg).strip()
            if not content:
                raise Exception(
                    "The model returned an empty message. Try again or change OPENROUTER_MODEL; "
                    "empty content can happen when a provider blocks the request or returns no completion."
                )
            return {
                "message": content,
                "canvas": canvas_with_output(existing_canvas, thread_id),
                "reply_duration_ms": _reply_duration_ms(),
                "strategy_name": last_strategy_name,
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
            elif name == RUN_STRATEGY_TOOL_NAME:
                limited = _trim_tool_payload_streams(
                    tool_payload,
                    RUN_STRATEGY_TOOL_MESSAGE_MAX_JSON,
                    "backtest_stdout",
                    "backtest_stderr",
                )
            chat_messages.append(
                ToolMessage(content=json.dumps(limited), tool_call_id=tid)
            )

    raise Exception("Agent stopped: maximum tool iterations reached without a final reply.")
