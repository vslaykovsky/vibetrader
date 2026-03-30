from __future__ import annotations
import dotenv
dotenv.load_dotenv()
from langsmith import traceable
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from services.chat_openrouter import ChatOpenRouter


SYSTEM_PROMPT = """You help users design trading strategies in chat.

- Before running update_strategy for the first time, ask the user to provide additional context needed to build the strategy if not provided yet. Exaples may include, but not limited to:
  - ticker
  - candlestick period
  - time period
  - stop loss
  - take profit
  - other parameters that are needed to build the strategy
- To change code or parameters, call update_strategy with a short task describing only what should change.
- To re-run the backtest without changing code, call rerun_backtest; pass ticker when a different symbol is needed, and optional candlestick_period (Alpaca timeframe, e.g. 1Day, 1Hour) and time_period (e.g. 8y, 252d, or days as an integer string) when the user asks.

Answer in plain text. No JSON or markup unless the user asks."""

STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "strategies"
STRATEGY_AGENTS_TEMPLATE = STRATEGIES_DIR / "AGENTS.md"
UPDATE_STRATEGY_TOOL_NAME = "update_strategy"
RERUN_BACKTEST_TOOL_NAME = "rerun_backtest"

ProgressCallback = Callable[[str], None] | None

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


def ensure_strategy_workspace(thread_id: str) -> Path:
    if not thread_id_allowed(thread_id):
        raise ValueError("invalid thread_id")
    workspace = STRATEGIES_DIR / thread_id
    workspace.mkdir(parents=True, exist_ok=True)
    dest_agents = workspace / "AGENTS.md"
    if not dest_agents.is_file() and STRATEGY_AGENTS_TEMPLATE.is_file():
        shutil.copy2(STRATEGY_AGENTS_TEMPLATE, dest_agents)
    return workspace


def strategy_root_for_thread(thread_id: str) -> Path:
    return ensure_strategy_workspace(thread_id)

AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": UPDATE_STRATEGY_TOOL_NAME,
            "description": (
                "Change the trading strategy in this thread's strategies/<thread_id>/ folder using "
                "Codex exec in full-auto mode, then re-run the backtest to refresh output/data.json."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Free-form instructions describing what to implement or change.",
                    }
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": RERUN_BACKTEST_TOOL_NAME,
            "description": (
                "Re-run python src/strategy.py --backtest for this thread only (no Codex, no code edits). "
                "Refreshes output/data.json. Omit ticker to use STRATEGY_BACKTEST_TICKER or SPY. "
                "Optional candlestick_period and time_period are passed as CLI flags when set."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Symbol to backtest (e.g. SPY, QQQ). Omit for the default ticker.",
                    },
                    "candlestick_period": {
                        "type": "string",
                        "description": "Alpaca bar timeframe (e.g. 1Day, 1Hour). Omit to use the strategy default.",
                    },
                    "time_period": {
                        "type": "string",
                        "description": "History window: Ny or Nd (e.g. 8y, 252d) or a plain integer days string. Omit for default.",
                    },
                },
                "required": [],
            },
        },
    },
]


def _tail(s: str, max_chars: int = 12_000) -> str:
    if len(s) <= max_chars:
        return s
    return s[-max_chars:]


def _strategy_output_file_key(filename: str) -> str:
    lower = filename.lower()
    if lower == "charts.js":
        return "charts.js"
    if lower == "data.json":
        return "data.json"
    return filename


def _read_strategy_output_dir(thread_id: str) -> dict[str, Any]:
    output_dir = strategy_root_for_thread(thread_id) / "output"
    if not output_dir.is_dir():
        return {}
    out: dict[str, Any] = {}
    for path in sorted(output_dir.iterdir()):
        if not path.is_file():
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


def canvas_with_output(existing_canvas: dict[str, Any], thread_id: str) -> dict[str, Any]:
    merged = dict(existing_canvas)
    if thread_id_allowed(thread_id):
        merged["output"] = _read_strategy_output_dir(thread_id)
    else:
        merged["output"] = {}
    return merged


@traceable(name="run_codex_exec")
def _run_codex_exec(task: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    cmd = [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--model", "gpt-5.4",
        "-c", "service_tier=fast",
        "-c", "model_verbosity=low",
        "-c", "features.fast_mode=true",
        task,
    ]
    return _run_logged_subprocess("codex exec", cmd, str(cwd), timeout=600)


@traceable(name="run_strategy_backtest")
def _run_strategy_backtest(
    ticker: str,
    cwd: Path,
    *,
    candlestick_period: str | None = None,
    time_period: str | None = None,
) -> subprocess.CompletedProcess[str]:
    backtest_cmd = [sys.executable, "src/strategy.py", "--ticker", ticker, "--backtest"]
    cp = (candlestick_period or "").strip()
    if cp:
        backtest_cmd.extend(["--candlestick-period", cp])
    tp = (time_period or "").strip()
    if tp:
        backtest_cmd.extend(["--time-period", tp])
    return _run_logged_subprocess("strategy backtest", backtest_cmd, str(cwd), timeout=300)


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
        on_progress("Updating strategy…")
    codegen = _run_codex_exec(task, root)
    result: dict[str, Any] = {
        "runner": "codex",
        "codex_returncode": codegen.returncode,
        "codex_stdout": _tail(codegen.stdout or ""),
        "codex_stderr": _tail(codegen.stderr or ""),
    }
    codegen_rc = codegen.returncode
    codegen_err = "codex exec failed"
    if on_progress:
        on_progress("Running backtest…")
    bt = _run_strategy_backtest(ticker, root)
    result["backtest_returncode"] = bt.returncode
    result["backtest_stdout"] = _tail(bt.stdout or "")
    result["backtest_stderr"] = _tail(bt.stderr or "")
    result["ok"] = codegen_rc == 0 and bt.returncode == 0
    if codegen_rc != 0:
        result["error"] = codegen_err
    elif bt.returncode != 0:
        result["error"] = "backtest failed"
    return result


@traceable(name="run_rerun_backtest")
def run_rerun_backtest(
    thread_id: str,
    ticker: str | None,
    *,
    candlestick_period: str | None = None,
    time_period: str | None = None,
    on_progress: ProgressCallback = None,
) -> dict[str, Any]:
    if not thread_id_allowed(thread_id):
        return {"ok": False, "error": "invalid thread_id"}
    root = ensure_strategy_workspace(thread_id)
    t = (ticker or "").strip()
    if not t:
        t = (os.getenv("STRATEGY_BACKTEST_TICKER") or "SPY").strip() or "SPY"
    cp = (candlestick_period or "").strip() or None
    tp = (time_period or "").strip() or None
    if on_progress:
        on_progress("Running backtest…")
    bt = _run_strategy_backtest(t, root, candlestick_period=cp, time_period=tp)
    result: dict[str, Any] = {
        "ticker": t,
        "backtest_returncode": bt.returncode,
        "backtest_stdout": _tail(bt.stdout or ""),
        "backtest_stderr": _tail(bt.stderr or ""),
        "ok": bt.returncode == 0,
    }
    if cp is not None:
        result["candlestick_period"] = cp
    if tp is not None:
        result["time_period"] = tp
    if bt.returncode != 0:
        result["error"] = "backtest failed"
    return result


def _tool_handlers_for_thread(
    thread_id: str, on_progress: ProgressCallback = None
) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    def _update(args: dict[str, Any]) -> dict[str, Any]:
        return run_update_strategy(thread_id, str(args.get("task", "")), on_progress=on_progress)

    def _rerun(args: dict[str, Any]) -> dict[str, Any]:
        raw_t = args.get("ticker")
        ticker = None if raw_t is None else str(raw_t)
        raw_cp = args.get("candlestick_period")
        cp = None if raw_cp is None else str(raw_cp)
        raw_tp = args.get("time_period")
        tp = None if raw_tp is None else str(raw_tp)
        return run_rerun_backtest(
            thread_id, ticker, candlestick_period=cp, time_period=tp, on_progress=on_progress
        )

    return {
        UPDATE_STRATEGY_TOOL_NAME: _update,
        RERUN_BACKTEST_TOOL_NAME: _rerun,
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
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()

    if not api_key:
        return {
            "message": (
                "OPENROUTER_API_KEY is not configured. Your message was saved. "
                "Set the key to enable live agent responses."
            ),
            "canvas": canvas_with_output(existing_canvas, thread_id),
        }

    chat_messages: list[BaseMessage] = [
        SystemMessage(content=SYSTEM_PROMPT),
        *_stored_messages_to_lc(messages),
    ]

    max_iterations = 10
    llm = ChatOpenRouter(
        model=model,
        openai_api_key=api_key,
        request_timeout=120,
        default_headers={
            "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:5173"),
            "X-Title": os.getenv("OPENROUTER_APP_NAME", "VibeTrader Strategy Builder"),
        },
    )
    llm_tools = llm.bind_tools(AGENT_TOOLS)
    tool_handlers = _tool_handlers_for_thread(thread_id, on_progress=on_progress)

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
            }
        for tc in tool_calls:
            name, parsed_args, tid = _tool_call_parts(tc)
            handler = tool_handlers.get(name)
            if handler is None:
                tool_payload: dict[str, Any] = {"ok": False, "error": f"unknown tool: {name}"}
            else:
                tool_payload = handler(parsed_args)
            chat_messages.append(
                ToolMessage(content=json.dumps(tool_payload), tool_call_id=tid)
            )

    raise Exception("Agent stopped: maximum tool iterations reached without a final reply.")
