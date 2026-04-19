from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path

from strategies_v2.utils import StrategyInput, StrategyOutput


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

    def start(self) -> StrategyOutput:
        script = self.workspace / self.entry_script
        if not script.is_file():
            raise StrategyRuntimeError(f"Strategy script not found: {script}")
        self._proc = subprocess.Popen(
            [self.python_executable, self.entry_script],
            cwd=str(self.workspace),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if self._proc.stdout is None:
            raise StrategyRuntimeError("stdout not available")
        self._reader_thread = threading.Thread(target=self._stdout_reader, daemon=True)
        self._reader_thread.start()
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
            return StrategyOutput.model_validate_json(line.strip())
        except Exception as exc:
            err = self._drain_stderr()
            raise StrategyRuntimeError(f"Invalid startup JSON: {exc!s}; stderr={err!r}") from exc

    def send(self, step: StrategyInput) -> StrategyOutput:
        if self._proc is None or self._proc.stdin is None:
            raise StrategyRuntimeError("Strategy process not started")
        line_out = step.model_dump_json()
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
            return StrategyOutput.model_validate_json(line.strip())
        except Exception as exc:
            err = self._drain_stderr()
            raise StrategyRuntimeError(f"Invalid response JSON: {exc!s}; stderr={err!r}") from exc

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
