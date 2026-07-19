from __future__ import annotations

from pathlib import Path

from application.services import rust_build


def test_build_selects_requested_release_binary(monkeypatch, tmp_path):
    (tmp_path / "Cargo.toml").write_text(
        """
[package]
name = "example"
version = "0.1.0"
edition = "2021"

[[bin]]
name = "example_worker"
path = "worker.rs"

[[bin]]
name = "example_simulator"
path = "simulator.rs"
""",
        encoding="utf-8",
    )
    for name in ("Cargo.lock", "strategy.rs", "utils.rs", "portfolio.rs", "simulator.rs"):
        (tmp_path / name).write_text("", encoding="utf-8")
    target = tmp_path / "target"
    executable = target / "release" / "example_simulator"
    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stderr = ""
        stdout = (
            '{"reason":"compiler-artifact","target":{"name":"example_simulator"},'
            f'"executable":"{executable}"}}\n'
        )

    def fake_run(command, **kwargs):
        calls.append(command)
        executable.parent.mkdir(parents=True)
        executable.write_text("binary", encoding="utf-8")
        return Completed()

    monkeypatch.setattr(rust_build, "rust_target_dir", lambda: target)
    monkeypatch.setattr(rust_build.shutil, "which", lambda _: "/usr/bin/cargo")
    monkeypatch.setattr(rust_build.subprocess, "run", fake_run)

    result = rust_build.build_rust_binary(tmp_path, "simulator.rs")

    assert result == executable
    assert calls[0][0] == "/usr/bin/cargo"
    assert "--release" in calls[0]
    assert calls[0][calls[0].index("--bin") + 1] == "example_simulator"


def test_template_uses_balanced_release_profile():
    manifest = (
        Path(__file__).resolve().parents[1] / "strategies_v2" / "Cargo.toml"
    ).read_text(encoding="utf-8")
    assert "opt-level = 2" in manifest
    assert "lto = false" in manifest
    assert "codegen-units = 8" in manifest
