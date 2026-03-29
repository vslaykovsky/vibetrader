from __future__ import annotations
import dotenv
dotenv.load_dotenv()
from langsmith import traceable
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from services.chat_openrouter import ChatOpenRouter


SYSTEM_PROMPT = """You are a trading strategy design agent.
You help the user create a trading strategy through chat.

When the user wants the strategy code or parameters changed, call update_strategy with a clear, self-contained task string for the coding agent.

Reply in plain text only. Do not use JSON or structured markup unless the user asks."""

STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "strategies"
STRATEGY_AGENTS_TEMPLATE = STRATEGIES_DIR / "AGENTS.md"
UPDATE_STRATEGY_TOOL_NAME = "update_strategy"


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
                "Change the trading strategy in this thread's strategies/<thread_id>/ folder. Runs "
                "Codex in full-auto mode with your task, then re-runs the backtest to "
                "refresh output/data.json."
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
    }
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
    codex_cmd = ["codex", "exec", "--full-auto", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", task]
    return subprocess.run(
        codex_cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=600,
    )


@traceable(name="run_strategy_backtest")
def _run_strategy_backtest(ticker: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    backtest_cmd = [sys.executable, "src/strategy.py", "--ticker", ticker, "--backtest"]
    return subprocess.run(
        backtest_cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=300,
    )


@traceable(name="run_update_strategy")
def run_update_strategy(thread_id: str, task: str) -> dict[str, Any]:
    task = (task or "").strip()
    if not task:
        return {"ok": False, "error": "task is empty"}
    if not thread_id_allowed(thread_id):
        return {"ok": False, "error": "invalid thread_id"}
    root = ensure_strategy_workspace(thread_id)
    ticker = (os.getenv("STRATEGY_BACKTEST_TICKER") or "SPY").strip() or "SPY"
    codex = _run_codex_exec(task, root)
    result: dict[str, Any] = {
        "codex_returncode": codex.returncode,
        "codex_stdout": _tail(codex.stdout or ""),
        "codex_stderr": _tail(codex.stderr or ""),
    }
    bt = _run_strategy_backtest(ticker, root)
    result["backtest_returncode"] = bt.returncode
    result["backtest_stdout"] = _tail(bt.stdout or "")
    result["backtest_stderr"] = _tail(bt.stderr or "")
    result["ok"] = codex.returncode == 0 and bt.returncode == 0
    if codex.returncode != 0:
        result["error"] = "codex exec failed"
    elif bt.returncode != 0:
        result["error"] = "backtest failed"
    return result


def _tool_handlers_for_thread(thread_id: str) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    def _update(args: dict[str, Any]) -> dict[str, Any]:
        return run_update_strategy(thread_id, str(args.get("task", "")))

    return {UPDATE_STRATEGY_TOOL_NAME: _update}


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
    tool_handlers = _tool_handlers_for_thread(thread_id)

    for _ in range(max_iterations):
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
