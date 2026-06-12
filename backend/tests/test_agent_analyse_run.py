import asyncio
import json

import httpx
from langchain_core.messages import AIMessage
from openrouter.components.toomanyrequestsresponseerrordata import (
    TooManyRequestsResponseErrorData as TooManyRequestsErrorComponent,
)
from openrouter.errors import (
    TooManyRequestsResponseError,
    TooManyRequestsResponseErrorData,
)

import services.agent as agent_module
from services.agent import (
    _codex_stdout_final_answer,
    _openrouter_rate_limit_delay_seconds,
    _run_chat_openrouter_ainvoke,
)


def test_codex_stdout_final_answer():
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "abc"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "The trade happened because RSI crossed the threshold.",
                            }
                        ],
                    },
                }
            ),
        ]
    )

    assert _codex_stdout_final_answer(stdout) == "The trade happened because RSI crossed the threshold."


def test_run_chat_openrouter_ainvoke_retries_timeouts():
    class SlowThenOk:
        def __init__(self):
            self.attempts = 0

        async def ainvoke(self, messages):
            self.attempts += 1
            if self.attempts <= 2:
                await asyncio.sleep(0.02)
            return AIMessage(content="ok")

    llm = SlowThenOk()

    msg = _run_chat_openrouter_ainvoke(llm, [], timeout_seconds=0.001, retries=3)

    assert msg.content == "ok"
    assert llm.attempts == 3


def _make_429_error(retry_after: str = "1") -> TooManyRequestsResponseError:
    raw_response = httpx.Response(
        429,
        headers={"retry-after": retry_after},
        content=b"{}",
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )
    data = TooManyRequestsResponseErrorData(
        error=TooManyRequestsErrorComponent(
            code=429,
            message="Provider returned error",
            metadata={"retry_after_seconds": 1},
        )
    )
    return TooManyRequestsResponseError(data=data, raw_response=raw_response)


def test_run_chat_openrouter_ainvoke_retries_rate_limits(monkeypatch):
    monkeypatch.setattr(
        agent_module, "_openrouter_rate_limit_delay_seconds", lambda e, attempt: 0.0
    )

    class RateLimitedThenOk:
        def __init__(self):
            self.attempts = 0

        async def ainvoke(self, messages):
            self.attempts += 1
            if self.attempts <= 2:
                raise _make_429_error()
            return AIMessage(content="ok")

    llm = RateLimitedThenOk()

    msg = _run_chat_openrouter_ainvoke(llm, [])

    assert msg.content == "ok"
    assert llm.attempts == 3


def test_openrouter_rate_limit_delay_honors_retry_after():
    err = _make_429_error(retry_after="2")
    assert _openrouter_rate_limit_delay_seconds(err, 1) == 2.0
    assert _openrouter_rate_limit_delay_seconds(err, 2) == 4.0
    assert _openrouter_rate_limit_delay_seconds(err, 10) == 30.0
