from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)


class RustBuildError(RuntimeError):
    pass


def rust_target_dir() -> Path:
    return Path(
        os.getenv("STRATEGY_RUST_TARGET_DIR")
        or str(Path(__file__).resolve().parents[2] / ".rust-target")
    ).resolve()


def _binary_target(workspace: Path, binary_source: str) -> tuple[str, Path]:
    manifest = workspace / "Cargo.toml"
    if not manifest.is_file():
        raise RustBuildError(f"Rust strategy manifest not found: {manifest}")
    try:
        cargo_config = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RustBuildError(f"Invalid Rust strategy manifest: {exc}") from exc
    bins = cargo_config.get("bin")
    if not isinstance(bins, list):
        raise RustBuildError("Cargo.toml must define [[bin]] targets")
    requested = Path(binary_source).as_posix()
    for target in bins:
        if not isinstance(target, dict):
            continue
        name = target.get("name")
        path = target.get("path")
        if isinstance(name, str) and isinstance(path, str) and Path(path).as_posix() == requested:
            suffix = ".exe" if os.name == "nt" else ""
            return name, rust_target_dir() / "release" / f"{name}{suffix}"
    raise RustBuildError(f"Cargo.toml has no binary target for {binary_source!r}")


def _cached_binary(workspace: Path, binary_source: str, executable: Path) -> Path | None:
    if not executable.is_file():
        return None
    inputs = [
        workspace / binary_source,
        workspace / "strategy.rs",
        workspace / "utils.rs",
        workspace / "portfolio.rs",
        workspace / "simulator.rs",
        workspace / "optimizer_runtime.rs",
        workspace / "Cargo.toml",
        workspace / "Cargo.lock",
    ]
    existing_inputs = [path for path in inputs if path.is_file()]
    if not existing_inputs:
        return None
    newest_input = max(path.stat().st_mtime_ns for path in existing_inputs)
    return executable if executable.stat().st_mtime_ns >= newest_input else None


def build_rust_binary(workspace: Path, binary_source: str) -> Path:
    workspace = Path(workspace).resolve()
    bin_name, expected_executable = _binary_target(workspace, binary_source)
    cached = _cached_binary(workspace, binary_source, expected_executable)
    if cached is not None:
        return cached

    cargo = (os.getenv("STRATEGY_RUST_CARGO") or "cargo").strip() or "cargo"
    cargo_path = shutil.which(cargo)
    if cargo_path is None:
        raise RustBuildError(
            f"Rust strategy selected but Cargo executable {cargo!r} was not found"
        )
    raw_timeout = (os.getenv("STRATEGY_RUST_BUILD_TIMEOUT_S") or "300").strip()
    try:
        timeout = max(1.0, float(raw_timeout))
    except ValueError:
        timeout = 300.0
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(rust_target_dir())
    cmd = [
        cargo_path,
        "build",
        "--release",
        "--locked",
        "--manifest-path",
        str(workspace / "Cargo.toml"),
        "--bin",
        bin_name,
        "--message-format=json-render-diagnostics",
    ]
    logger.info(
        "build_rust_binary cwd=%s source=%s bin=%s target_dir=%s",
        workspace,
        binary_source,
        bin_name,
        rust_target_dir(),
    )
    try:
        built = subprocess.run(
            cmd,
            cwd=str(workspace),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RustBuildError(f"Rust build exceeded {timeout:g}s") from exc

    executable = ""
    rendered: list[str] = []
    for line in (built.stdout or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        message = event.get("message")
        if isinstance(message, dict) and isinstance(message.get("rendered"), str):
            rendered.append(message["rendered"])
        target = event.get("target")
        candidate = event.get("executable")
        if (
            event.get("reason") == "compiler-artifact"
            and isinstance(target, dict)
            and target.get("name") == bin_name
            and isinstance(candidate, str)
            and candidate
        ):
            executable = candidate
    if built.returncode != 0:
        detail = "\n".join(rendered) or built.stderr or built.stdout
        raise RustBuildError(
            f"Rust build failed (exit={built.returncode}): {detail[-8000:]}"
        )
    result = Path(executable) if executable else expected_executable
    if not result.is_file():
        raise RustBuildError("Cargo completed without producing the requested binary")
    return result
