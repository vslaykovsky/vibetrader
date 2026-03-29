from __future__ import annotations

import os

from langchain_core.utils import secret_from_env
from langchain_openai import ChatOpenAI
from pydantic import Field, SecretStr


class ChatOpenRouter(ChatOpenAI):
    openai_api_key: SecretStr | None = Field(
        alias="api_key", default_factory=secret_from_env("OPENROUTER_API_KEY")
    )

    @property
    def lc_secrets(self) -> dict:
        return {"openai_api_key": "OPENROUTER_API_KEY"}

    def __init__(self, openai_api_key=None, **kwargs):
        key = openai_api_key or os.getenv("OPENROUTER_API_KEY")
        super().__init__(
            base_url="https://openrouter.ai/api/v1", openai_api_key=key, **kwargs
        )
