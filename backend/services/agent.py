from __future__ import annotations
from datetime import datetime
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


SYSTEM_PROMPT = f"""You help users design trading strategies in chat.

Workflow

* Before the first update_strategy, request any missing details needed to build the strategy (e.g., ticker, candlestick period, time range, stop loss, take profit, other parameters).
* To modify code call update_strategy with a brief task describing only the changes. Match the user’s language. Only run update_strategy when strategy logic or structure must change. Never call update_strategy when the user only wants to try different values for parameters that already exist in output/params.json (or are already defined by the strategy’s parameter model); use run_strategy instead.
* If the user only tweaks existing parameters (different ticker, dates, thresholds, etc.), call run_strategy with python src/strategy.py --backtest and pass overrides as a single --params argument whose value is a JSON object (shell-escaped), merged over output/params.json for that run. Do not call update_strategy for that.
* To re-run a backtest with the saved params file unchanged, call run_strategy with e.g. python src/strategy.py --backtest. Use the strategy script's --help output if needed.
* To run parameter optimization, call run_strategy with python src/strategy.py --hyperopt (if the strategy implements it).
Always respond in the user’s language.
* rerun backtest after update_strategy to refresh output/data.json.

Notes

* update_strategy generates strategy code and charts; it can also include hyperparameter optimization code.
* Strategies can use Alpaca market data.
* Backtesting is supported; live trading is not.
* Today's date is {datetime.now().strftime("%Y-%m-%d")}.

{{strategy_help}}

Answer in plain text. No JSON or markup unless the user asks."""

STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "strategies"
STRATEGY_AGENTS_TEMPLATE = STRATEGIES_DIR / "AGENTS.md"
UPDATE_STRATEGY_TOOL_NAME = "update_strategy"
RUN_STRATEGY_TOOL_NAME = "run_strategy"
ANALYSE_CODE_TOOL_NAME = "analyse_code"

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


def read_strategy_code(thread_id: str) -> str:
    root = ensure_strategy_workspace(thread_id)
    path = root / "src" / "strategy.py"
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

    src_dir = root / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "strategy.py").write_text(code or "", encoding="utf-8")

    output_dir = root / "output"
    shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not isinstance(canvas, dict):
        return
    output = canvas.get("output")
    if not isinstance(output, dict):
        return

    for filename, contents in output.items():
        if not isinstance(filename, str) or not filename:
            continue
        out_path = output_dir / filename
        if out_path.name != filename:
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
            "name": RUN_STRATEGY_TOOL_NAME,
            "description": (
                "Run a strategy command in this thread's workspace (no Codex, no code edits). "
                "Use python src/strategy.py --backtest; override params with --params '<JSON object>' (merged over output/params.json). "
                "Use python src/strategy.py --hyperopt when optimizing. "
                "Refreshes output/data.json on success."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "Full shell command, e.g. python src/strategy.py --backtest or "
                            "python src/strategy.py --backtest --params '{\"ticker\":\"SPY\",\"fast_period\":12}'"
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
                "Answer a question about the current strategy's code/params in this thread. "
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


def _read_strategy_params_text(thread_id: str) -> str:
    root = ensure_strategy_workspace(thread_id)
    params_path = root / "output" / "params.json"
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
    api_key: str,
) -> dict[str, Any]:
    q = (question or "").strip()
    if not q:
        return {"ok": False, "error": "question is empty"}
    if not thread_id_allowed(thread_id):
        return {"ok": False, "error": "invalid thread_id"}
    if not (api_key or "").strip():
        return {"ok": False, "error": "OPENROUTER_API_KEY is not configured"}

    code = read_strategy_code(thread_id)
    params_text = _read_strategy_params_text(thread_id)

    analysis_system = (
        "You are a code analyst for a trading strategy project. "
        "Answer the user's question using ONLY the provided strategy code and params. "
        "Be concise: 1-4 sentences. If the answer cannot be determined, say what is missing."
    )
    context = (
        "Strategy params (JSON, may be empty):\n"
        f"{_tail(params_text, 40_000)}\n\n"
        "Strategy code (Python, may be empty):\n"
        f"{_tail(code, 80_000)}"
    )

    llm = ChatOpenRouter(
        model=model,
        openai_api_key=api_key,
        request_timeout=120,
        default_headers={
            "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:5173"),
            "X-Title": os.getenv("OPENROUTER_APP_NAME", "VibeTrader Strategy Builder"),
        },
    )
    msg = llm.invoke(
        [
            SystemMessage(content=analysis_system),
            SystemMessage(content=context),
            HumanMessage(content=q),
        ]
    )
    answer = _aimessage_plain_text(msg).strip()
    return {"ok": True, "answer": answer}


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
        existing_output = merged.get("output")
        if not isinstance(existing_output, dict):
            existing_output = {}
        disk_output = _read_strategy_output_dir(thread_id)
        merged["output"] = {**existing_output, **disk_output} if disk_output else dict(existing_output)
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
    command: str,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    import shlex
    parts = shlex.split(command)
    if parts and parts[0] == "python":
        parts[0] = sys.executable
    timeout_s = int(os.getenv("STRATEGY_BACKTEST_TIMEOUT_S", "1800"))
    return _run_logged_subprocess("strategy backtest", parts, str(cwd), timeout=timeout_s)


def _strategy_script_help(workspace: Path) -> str:
    script = workspace / "src" / "strategy.py"
    if not script.is_file():
        return ""
    try:
        proc = subprocess.run(
            [sys.executable, "src/strategy.py", "--help"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return _tail((proc.stdout or "").strip())


def _generate_pseudocode_diff(root: Path) -> None:
    pseudocode = root / "output" / "pseudocode.txt"
    pseudocode_old = root / "output" / "pseudocode.old"
    pseudocode_diff = root / "output" / "pseudocode.diff"
    if not pseudocode_old.is_file():
        pseudocode_diff.unlink(missing_ok=True)
        return
    try:
        proc = subprocess.run(
            ["diff", "-u", str(pseudocode_old), str(pseudocode)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        diff_text = proc.stdout or ""
        if diff_text.strip():
            pseudocode_diff.write_text(diff_text, encoding="utf-8")
        else:
            pseudocode_diff.unlink(missing_ok=True)
    except (OSError, subprocess.TimeoutExpired):
        pseudocode_diff.unlink(missing_ok=True)
    finally:
        pseudocode_old.unlink(missing_ok=True)


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

    pseudocode = root / "output" / "pseudocode.txt"
    pseudocode_old = root / "output" / "pseudocode.old"
    if pseudocode.is_file():
        shutil.copy2(pseudocode, pseudocode_old)

    if on_progress:
        on_progress("Updating strategy…")
    codegen = _run_codex_exec(task, root)

    _generate_pseudocode_diff(root)

    result: dict[str, Any] = {
        "runner": "codex",
        "codex_returncode": codegen.returncode,
        "codex_stdout": _tail(codegen.stdout or ""),
        "codex_stderr": _tail(codegen.stderr or ""),
        "ok": codegen.returncode == 0,
    }
    if codegen.returncode != 0:
        result["error"] = "codex exec failed"
    return result


@traceable(name="run_rerun_backtest")
def run_rerun_backtest(
    thread_id: str,
    command: str,
    on_progress: ProgressCallback = None,
) -> dict[str, Any]:
    if not thread_id_allowed(thread_id):
        return {"ok": False, "error": "invalid thread_id"}
    command = (command or "").strip()
    if not command:
        return {"ok": False, "error": "command is empty"}
    root = ensure_strategy_workspace(thread_id)
    if on_progress:
        on_progress("Running backtest…")
    bt = _run_strategy_backtest(command, root)
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
    api_key: str,
    on_progress: ProgressCallback = None,
) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    def _update(args: dict[str, Any]) -> dict[str, Any]:
        return run_update_strategy(thread_id, str(args.get("task", "")), on_progress=on_progress)

    def _rerun(args: dict[str, Any]) -> dict[str, Any]:
        command = str(args.get("command", ""))
        return run_rerun_backtest(thread_id, command, on_progress=on_progress)

    def _analyse(args: dict[str, Any]) -> dict[str, Any]:
        return run_analyse_code(
            thread_id=thread_id,
            question=str(args.get("question", "")),
            model=model,
            api_key=api_key,
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
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()

    if not api_key:
        return {
            "message": (
                "OPENROUTER_API_KEY is not configured. Your message was saved. "
                "Set the key to enable live agent responses."
            ),
            "canvas": canvas_with_output(existing_canvas, thread_id),
        }

    workspace = strategy_root_for_thread(thread_id)
    strategy_help = _strategy_script_help(workspace)
    if strategy_help:
        strategy_parameters = ""
        params_path = workspace / "output" / "params.json"
        if params_path.is_file():
            with open(params_path, "r", encoding="utf-8") as f:
                strategy_parameters = f.read()
        pseudocode = workspace / "output" / "pseudocode.txt"
        if pseudocode.is_file():
            with open(pseudocode, "r", encoding="utf-8") as f:
                pseudocode = f.read()
        strategy_help = f"""Help message of the current strategy script:
python src/strategy.py --help
{strategy_help}

Current strategy parameters (can be overridden with --params JSON argument):
{strategy_parameters}

Current strategy pseudocode:
{pseudocode}
"""
    else:
        strategy_help = "Note that script src/strategy.py hasn't been generated yet. Need to run update_strategy first."
    chat_messages: list[BaseMessage] = [
        SystemMessage(content=SYSTEM_PROMPT.format(strategy_help=strategy_help)),
        *_stored_messages_to_lc(messages),
    ]

    max_iterations = 20
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
    tool_handlers = _tool_handlers_for_thread(
        thread_id,
        model=model,
        api_key=api_key,
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
            chat_messages.append(
                ToolMessage(content=json.dumps(tool_payload), tool_call_id=tid)
            )

    raise Exception("Agent stopped: maximum tool iterations reached without a final reply.")
