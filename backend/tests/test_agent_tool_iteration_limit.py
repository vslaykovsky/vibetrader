from langchain_core.messages import AIMessage

from services import agent


def test_agent_prompt_requires_upfront_tool_call_estimate():
    assert agent.AGENT_MAX_TOOL_ITERATIONS == 50
    assert "estimate the total number of tool calls needed" in agent.SYSTEM_PROMPT
    assert "greater than 50" in agent.SYSTEM_PROMPT
    assert "do not invoke any tools" in agent.SYSTEM_PROMPT
    assert "split it into smaller requests" in agent.SYSTEM_PROMPT


def test_tool_iteration_limit_returns_and_streams_fixed_message(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(agent, "AGENT_MAX_TOOL_ITERATIONS", 2)
    monkeypatch.setattr(
        agent,
        "ensure_strategy_source_language",
        lambda *_args, **_kwargs: {"ok": True, "codex_thread_id": ""},
    )
    monkeypatch.setattr(agent, "strategy_root_for_thread", lambda _thread_id: tmp_path)
    monkeypatch.setattr(agent, "_strategy_help_for_workspace", lambda _workspace: "help")
    monkeypatch.setattr(agent, "_tool_handlers_for_thread", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(agent, "canvas_with_output", lambda canvas, _thread_id: canvas)

    class DummyChatOpenRouter:
        def __init__(self, **_kwargs):
            pass

        def bind_tools(self, _tools):
            return self

    monkeypatch.setattr(agent, "ChatOpenRouter", DummyChatOpenRouter)

    invocation_count = 0

    def fake_invoke(*_args, **_kwargs):
        nonlocal invocation_count
        invocation_count += 1
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "unknown_tool",
                    "args": {},
                    "id": f"tool-{invocation_count}",
                    "type": "tool_call",
                }
            ],
        )

    monkeypatch.setattr(agent, "_invoke_agent_model", fake_invoke)
    streamed: list[str] = []

    result = agent.build_agent_reply(
        messages=[],
        existing_canvas={"existing": True},
        thread_id="thread-id",
        on_token=streamed.append,
    )

    assert invocation_count == 2
    assert result["message"] == agent.TOOL_ITERATION_LIMIT_REACHED_MESSAGE
    assert result["canvas"] == {"existing": True}
    assert streamed == [agent.TOOL_ITERATION_LIMIT_REACHED_MESSAGE]
