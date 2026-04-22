from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from strategies_v2.utils import StrategyInput, StrategyOutput

logger = logging.getLogger(__name__)


class StrategyRuntimeError(RuntimeError):
    pass


class StrategyRuntime:
    """Runs ``strategy.py`` (or another entry) in a subprocess; first stdout line is startup ``StrategyOutput``."""

    def __init__(
        self,
        workspace: Path,
        *,
        entry_script: str = "strategy.py",
        startup_timeout_seconds: float = 60.0,
        response_timeout_seconds: float = 5.0,
        python_executable: str | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.entry_script = entry_script
        self.startup_timeout_seconds = startup_timeout_seconds
        self.response_timeout_seconds = response_timeout_seconds
        self.python_executable = python_executable or sys.executable
        self._proc: subprocess.Popen[str] | None = None
        self._out_q: queue.Queue[str | None] = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._recorded_inputs: list[str] = []
        self._recorded_outputs: list[str] = []

    @property
    def recorded_inputs(self) -> list[str]:
        return list(self._recorded_inputs)

    @property
    def recorded_outputs(self) -> list[str]:
        return list(self._recorded_outputs)

    def write_io_files(
        self,
        *,
        inputs_path: Path | None = None,
        outputs_path: Path | None = None,
    ) -> tuple[Path | None, Path | None]:
        in_path = inputs_path or (self.workspace / "inputs.json")
        out_path = outputs_path or (self.workspace / "outputs.json")

        in_payload = [StrategyInput.model_validate_json(s).model_dump(mode="json") for s in self._recorded_inputs]
        out_payload = [
            StrategyOutput.model_validate_json(s).model_dump(mode="json") for s in self._recorded_outputs
        ]
        in_path.write_text(
            __import__("json").dumps(in_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        out_path.write_text(
            __import__("json").dumps(out_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return in_path, out_path

    def _stdout_reader(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                self._out_q.put(line)
        finally:
            self._out_q.put(None)

    def start(self, *, initial_input: StrategyInput | None = None) -> StrategyOutput:
        script = self.workspace / self.entry_script
        if not script.is_file():
            raise StrategyRuntimeError(f"Strategy script not found: {script}")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        self._proc = subprocess.Popen(
            [self.python_executable, "-u", self.entry_script],
            cwd=str(self.workspace),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        if self._proc.stdout is None:
            raise StrategyRuntimeError("stdout not available")
        self._reader_thread = threading.Thread(target=self._stdout_reader, daemon=True)
        self._reader_thread.start()

        if initial_input is not None:
            if self._proc.stdin is None:
                raise StrategyRuntimeError("stdin not available")
            try:
                line_out = initial_input.model_dump_json()
                self._recorded_inputs.append(line_out)
                self._proc.stdin.write(line_out + "\n")
                self._proc.stdin.flush()
            except BrokenPipeError as exc:
                err = self._drain_stderr()
                raise StrategyRuntimeError(
                    f"Broken pipe writing initial input to strategy. stderr={err!r}"
                ) from exc

        logger.info(
            "await_strategy_first_stdout cwd=%s entry=%s pid=%s timeout_s=%s",
            self.workspace,
            self.entry_script,
            self._proc.pid,
            self.startup_timeout_seconds,
        )
        try:
            line = self._out_q.get(timeout=self.startup_timeout_seconds)
        except queue.Empty as exc:
            err = self._drain_stderr()
            raise StrategyRuntimeError(
                f"No startup line within {self.startup_timeout_seconds}s. stderr={err!r}"
            ) from exc
        if line is None:
            err = self._drain_stderr()
            raise StrategyRuntimeError(f"Empty startup line from strategy. stderr={err!r}")
        try:
            self._recorded_outputs.append(line.strip())
            return StrategyOutput.model_validate_json(line.strip())
        except Exception as exc:
            err = self._drain_stderr()
            raise StrategyRuntimeError(f"Invalid startup JSON: {exc!s}; stderr={err!r}") from exc

    def send(self, step: StrategyInput) -> StrategyOutput:
        if self._proc is None or self._proc.stdin is None:
            raise StrategyRuntimeError("Strategy process not started")
        line_out = step.model_dump_json()
        self._recorded_inputs.append(line_out)
        try:
            self._proc.stdin.write(line_out + "\n")
            self._proc.stdin.flush()
        except BrokenPipeError as exc:
            err = self._drain_stderr()
            raise StrategyRuntimeError(f"Broken pipe writing to strategy. stderr={err!r}") from exc
        try:
            line = self._out_q.get(timeout=self.response_timeout_seconds)
        except queue.Empty as exc:
            err = self._drain_stderr()
            raise StrategyRuntimeError(
                f"No stdout line within {self.response_timeout_seconds}s after send. stderr={err!r}"
            ) from exc
        if line is None:
            err = self._drain_stderr()
            code = self._proc.poll()
            raise StrategyRuntimeError(
                f"Strategy stdout closed before response (exit={code}). stderr={err!r}"
            )
        try:
            self._recorded_outputs.append(line.strip())
            return StrategyOutput.model_validate_json(line.strip())
        except Exception as exc:
            err = self._drain_stderr()
            raise StrategyRuntimeError(f"Invalid response JSON: {exc!s}; stderr={err!r}") from exc

    def finalize(self, *, timeout_seconds: float = 60.0) -> StrategyOutput:
        """Close stdin and drain any remaining stdout lines until the process exits.

        EDA strategies detect stdin EOF and then emit a final stdout line with
        ``OutputChart`` items (accumulated analytics). Call this once after the last
        ``send`` to collect those final outputs. Non-EDA strategies that emit nothing
        after EOF return an empty ``StrategyOutput``.
        """
        if self._proc is None:
            return StrategyOutput([])
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except Exception:
            pass
        collected: list[object] = []
        import queue as _queue
        import time as _time

        deadline = _time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                break
            try:
                line = self._out_q.get(timeout=remaining)
            except _queue.Empty:
                break
            if line is None:
                break
            s = line.strip()
            if not s:
                continue
            try:
                self._recorded_outputs.append(s)
                parsed = StrategyOutput.model_validate_json(s)
            except Exception as exc:
                err = self._drain_stderr()
                raise StrategyRuntimeError(
                    f"Invalid final JSON from strategy: {exc!s}; stderr={err!r}"
                ) from exc
            collected.extend(parsed.root)
        try:
            self._proc.wait(timeout=max(0.0, deadline - _time.monotonic()))
        except Exception:
            pass
        return StrategyOutput(collected)

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        self._reader_thread = None

    def _drain_stderr(self) -> str:
        if self._proc is None or self._proc.stderr is None:
            return ""
        try:
            return self._proc.stderr.read()[-4000:]
        except Exception:
            return ""

    def __enter__(self) -> StrategyRuntime:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
