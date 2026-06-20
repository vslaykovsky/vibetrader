from __future__ import annotations

import os
import signal
import subprocess
import threading
import time


class AgentRunCancelled(Exception):
    pass


def kill_subprocess_tree(proc: subprocess.Popen[str]) -> None:
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


class AgentRunControl:
    def __init__(self) -> None:
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen[str]] = set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def cancel(self) -> None:
        self.cancel_event.set()
        with self._lock:
            processes = list(self._processes)
        for proc in processes:
            kill_subprocess_tree(proc)

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise AgentRunCancelled("Strategy run stopped")

    def register_process(self, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            if self.cancelled:
                should_cancel = True
            else:
                should_cancel = False
            self._processes.add(proc)
        if should_cancel:
            kill_subprocess_tree(proc)
            self.unregister_process(proc)
            raise AgentRunCancelled("Strategy run stopped")

    def unregister_process(self, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            self._processes.discard(proc)


_controls_lock = threading.Lock()
_controls: dict[str, AgentRunControl] = {}


def register_agent_run(run_id: str) -> AgentRunControl:
    rid = str(run_id or "").strip()
    if not rid:
        raise ValueError("run_id is required")
    control = AgentRunControl()
    with _controls_lock:
        _controls[rid] = control
    return control


def unregister_agent_run(run_id: str, control: AgentRunControl | None = None) -> None:
    rid = str(run_id or "").strip()
    if not rid:
        return
    with _controls_lock:
        current = _controls.get(rid)
        if control is None or current is control:
            _controls.pop(rid, None)


def get_agent_run_control(run_id: str) -> AgentRunControl | None:
    rid = str(run_id or "").strip()
    if not rid:
        return None
    with _controls_lock:
        return _controls.get(rid)


def cancel_agent_run(run_id: str) -> bool:
    control = get_agent_run_control(run_id)
    if control is None:
        return False
    control.cancel()
    return True
