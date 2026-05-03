from services.agent import (
    CODEX_MODEL,
    CODEX_REASONING_EFFORT,
    _codex_exec_command,
    _codex_thread_id_from_stdout,
)


def test_codex_thread_id_from_stdout():
    stdout = '\n'.join(
        [
            '{"type":"thread.started","thread_id":"019dec74-c999-79c1-b268-e48e9d3fcfce"}',
            '{"type":"turn.started"}',
        ]
    )
    assert _codex_thread_id_from_stdout(stdout) == "019dec74-c999-79c1-b268-e48e9d3fcfce"


def test_codex_exec_command_places_exec_options_before_resume():
    cmd = _codex_exec_command(
        "change the strategy",
        "/tmp/strategy",
        "019decc1-9ab1-7a42-9022-2e2db09212f8",
        "--full-auto",
    )
    resume_index = cmd.index("resume")
    assert cmd[:resume_index] == [
        "codex",
        "exec",
        "--json",
        "--full-auto",
        "-C",
        "/tmp/strategy",
        "--skip-git-repo-check",
        "-c",
        "service_tier=fast",
        "-c",
        f"model={CODEX_MODEL}",
        "-c",
        "model_verbosity=low",
        "-c",
        f"model_reasoning_effort={CODEX_REASONING_EFFORT}",
        "-c",
        "features.fast_mode=true",
    ]
    assert cmd[resume_index:] == [
        "resume",
        "019decc1-9ab1-7a42-9022-2e2db09212f8",
        "change the strategy",
    ]
