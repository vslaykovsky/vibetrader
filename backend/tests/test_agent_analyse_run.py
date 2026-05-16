import asyncio
import json

from langchain_core.messages import AIMessage

from services.agent import _codex_stdout_final_answer, _run_chat_openrouter_ainvoke


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
