from __future__ import annotations

import json
import logging
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import msgpack

from application.services.strategy_engine import (
    StrategyEngine,
    StrategyTransport,
    configured_strategy_transport,
    engine_for_entrypoint,
)
from application.services.rust_build import RustBuildError, build_rust_binary
from strategies_v2.utils import StrategyInput, StrategyOutput

logger = logging.getLogger(__name__)

_FRAME_HEADER = struct.Struct(">I")
_DEFAULT_MAX_FRAME_BYTES = 64 * 1024 * 1024


class StrategyRuntimeError(RuntimeError):
    pass


class StrategyBuildError(StrategyRuntimeError):
    pass


class StrategyRuntime:
    """Run an interactive generated-strategy worker and validate its I/O contract.

    Historical Rust backtests do not use this per-event boundary; simulator.rs
    links the generated handler in-process. Python and paced Rust workers default
    to JSON-lines, with framed MessagePack available to Rust workers.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        entry_script: str = "strategy.py",
        startup_timeout_seconds: float = 60.0,
        response_timeout_seconds: float = 5.0,
        python_executable: str | None = None,
        transport: StrategyTransport | str | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.entry_script = entry_script
        self.startup_timeout_seconds = startup_timeout_seconds
        self.response_timeout_seconds = response_timeout_seconds
        self.python_executable = python_executable or sys.executable
        self.engine = engine_for_entrypoint(entry_script)
        if transport is None:
            self.transport = configured_strategy_transport(self.engine)
        else:
            try:
                self.transport = StrategyTransport(str(transport))
            except ValueError as exc:
                raise ValueError(f"unsupported strategy transport: {transport!r}") from exc
        self._proc: subprocess.Popen[bytes] | None = None
        self._out_q: queue.Queue[bytes | None] = queue.Queue()
        self._err_q: queue.Queue[str | None] = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._recorded_inputs: list[str] = []
        self._recorded_outputs: list[str] = []
        self._protocol_error: str = ""

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
            json.dumps(in_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        out_path.write_text(
            json.dumps(out_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return in_path, out_path

    def _max_frame_bytes(self) -> int:
        raw = (os.getenv("STRATEGY_MAX_FRAME_BYTES") or "").strip()
        try:
            return max(1024, int(raw)) if raw else _DEFAULT_MAX_FRAME_BYTES
        except ValueError:
            return _DEFAULT_MAX_FRAME_BYTES

    @staticmethod
    def _read_exact(stream: Any, size: int) -> bytes | None:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = stream.read(remaining)
            if not chunk:
                if not chunks:
                    return None
                raise EOFError(f"stream closed with {remaining} bytes still expected")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _stdout_reader(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            if self.transport is StrategyTransport.JSONL:
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    self._out_q.put(line)
            else:
                max_size = self._max_frame_bytes()
                while True:
                    header = self._read_exact(proc.stdout, _FRAME_HEADER.size)
                    if header is None:
                        break
                    (size,) = _FRAME_HEADER.unpack(header)
                    if size > max_size:
                        raise ValueError(
                            f"strategy frame is {size} bytes; limit is {max_size}"
                        )
                    payload = self._read_exact(proc.stdout, size)
                    if payload is None:
                        raise EOFError(f"stream closed before {size}-byte frame payload")
                    self._out_q.put(payload)
        except Exception as exc:
            self._protocol_error = str(exc)
        finally:
            self._out_q.put(None)

    def _stderr_reader(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                line = proc.stderr.readline()
                if not line:
                    break
                self._err_q.put(line.decode("utf-8", errors="replace"))
        finally:
            self._err_q.put(None)

    def _process_command(self, script: Path) -> list[str]:
        if self.engine is StrategyEngine.RUST:
            try:
                return [str(build_rust_binary(self.workspace, "worker.rs"))]
            except RustBuildError as exc:
                raise StrategyBuildError(str(exc)) from exc
        return [self.python_executable, "-u", self.entry_script]

    def start(self) -> StrategyOutput:
        script = self.workspace / self.entry_script
        if not script.is_file():
            raise StrategyRuntimeError(f"Strategy script not found: {script}")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["STRATEGY_IPC_TRANSPORT"] = self.transport.value
        command = self._process_command(script)
        self._proc = subprocess.Popen(
            command,
            cwd=str(self.workspace),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            env=env,
        )
        if self._proc.stdout is None:
            raise StrategyRuntimeError("stdout not available")
        self._reader_thread = threading.Thread(target=self._stdout_reader, daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._stderr_reader, daemon=True)
        self._stderr_thread.start()

        logger.info(
            "await_strategy_first_output cwd=%s entry=%s engine=%s transport=%s pid=%s timeout_s=%s",
            self.workspace,
            self.entry_script,
            self.engine.value,
            self.transport.value,
            self._proc.pid,
            self.startup_timeout_seconds,
        )
        try:
            payload = self._out_q.get(timeout=self.startup_timeout_seconds)
        except queue.Empty as exc:
            err = self._drain_stderr()
            raise StrategyRuntimeError(
                f"No startup output within {self.startup_timeout_seconds}s. stderr={err!r}"
            ) from exc
        if payload is None:
            err = self._drain_stderr()
            protocol = f" protocol_error={self._protocol_error!r}" if self._protocol_error else ""
            raise StrategyRuntimeError(f"Empty startup output from strategy.{protocol} stderr={err!r}")
        return self._parse_output(payload, label="startup")

    def _parse_output(self, payload: bytes, *, label: str) -> StrategyOutput:
        try:
            if self.transport is StrategyTransport.JSONL:
                raw = payload.strip()
                output = StrategyOutput.model_validate_json(raw)
            else:
                unpacked = msgpack.unpackb(payload, raw=False, strict_map_key=False)
                output = StrategyOutput.model_validate(unpacked)
            self._recorded_outputs.append(output.model_dump_json())
            return output
        except Exception as exc:
            err = self._drain_stderr()
            wire_name = "JSON" if self.transport is StrategyTransport.JSONL else "MessagePack"
            raise StrategyRuntimeError(
                f"Invalid {label} {wire_name}: {exc!s}; stderr={err!r}"
            ) from exc

    def drain_stdout(self, *, timeout_seconds: float) -> list[StrategyOutput]:
        outputs: list[StrategyOutput] = []
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                payload = self._out_q.get(timeout=remaining)
            except queue.Empty:
                break
            if payload is None:
                self._out_q.put(None)
                if self._protocol_error:
                    raise StrategyRuntimeError(
                        f"Invalid stdout framing: {self._protocol_error}; stderr={self._drain_stderr()!r}"
                    )
                break
            outputs.append(self._parse_output(payload, label="stdout"))
        return outputs

    def _encode_input(self, step: StrategyInput) -> bytes:
        if self.transport is StrategyTransport.JSONL:
            return step.model_dump_json().encode("utf-8") + b"\n"
        body = msgpack.packb(step.model_dump(mode="json"), use_bin_type=True)
        if len(body) > 0xFFFFFFFF:
            raise StrategyRuntimeError("strategy input is too large for MessagePack framing")
        return _FRAME_HEADER.pack(len(body)) + body

    def send(self, step: StrategyInput) -> StrategyOutput:
        if self._proc is None or self._proc.stdin is None:
            raise StrategyRuntimeError("Strategy process not started")
        self._recorded_inputs.append(step.model_dump_json())
        try:
            self._proc.stdin.write(self._encode_input(step))
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            err = self._drain_stderr()
            raise StrategyRuntimeError(f"Broken pipe writing to strategy. stderr={err!r}") from exc
        try:
            payload = self._out_q.get(timeout=self.response_timeout_seconds)
        except queue.Empty as exc:
            err = self._drain_stderr()
            raise StrategyRuntimeError(
                f"No stdout output within {self.response_timeout_seconds}s after send. stderr={err!r}"
            ) from exc
        if payload is None:
            err = self._drain_stderr()
            code = self._proc.poll()
            protocol = f" framing_error={self._protocol_error!r}" if self._protocol_error else ""
            raise StrategyRuntimeError(
                f"Strategy stdout closed before response (exit={code}).{protocol} stderr={err!r}"
            )
        output = self._parse_output(payload, label="response")
        self._validate_time_ack(output, step.unixtime)
        return output

    def _validate_time_ack(self, output: StrategyOutput, expected_unixtime: int) -> None:
        acks = [p for p in output.root if getattr(p, "kind", None) == "time_ack"]
        if len(acks) != 1:
            raise StrategyRuntimeError(
                f"Expected exactly one time_ack for unixtime={expected_unixtime}, got {len(acks)}"
            )
        if acks[0].unixtime != expected_unixtime:
            raise StrategyRuntimeError(
                f"Expected time_ack unixtime={expected_unixtime}, got {acks[0].unixtime}"
            )

    def finalize(self, *, timeout_seconds: float = 60.0) -> StrategyOutput:
        """Close strategy input and collect optional final charts/model output."""
        if self._proc is None:
            return StrategyOutput([])
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except Exception:
            pass
        collected: list[object] = []
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                payload = self._out_q.get(timeout=remaining)
            except queue.Empty:
                break
            if payload is None:
                if self._protocol_error:
                    raise StrategyRuntimeError(
                        f"Invalid final stdout framing: {self._protocol_error}; stderr={self._drain_stderr()!r}"
                    )
                break
            parsed = self._parse_output(payload, label="final")
            collected.extend(parsed.root)
        try:
            self._proc.wait(timeout=max(0.0, deadline - time.monotonic()))
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
        self._stderr_thread = None

    def _drain_stderr(self) -> str:
        if self._proc is None:
            return ""
        chunks: list[str] = []
        try:
            while True:
                line = self._err_q.get_nowait()
                if line is None:
                    break
                chunks.append(line)
        except queue.Empty:
            pass
        try:
            if self._proc.poll() is not None:
                self._stderr_thread and self._stderr_thread.join(timeout=0.2)
                while True:
                    line = self._err_q.get_nowait()
                    if line is None:
                        break
                    chunks.append(line)
        except queue.Empty:
            pass
        except Exception:
            pass
        return "".join(chunks)[-4000:]

    def __enter__(self) -> StrategyRuntime:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
