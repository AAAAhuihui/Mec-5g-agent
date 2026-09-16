from __future__ import annotations

import json
from typing import Any

from app.config import settings


class DeepSeekClient:
    """OpenAI SDK 兼容方式调用 DeepSeek，失败时返回 None 让上层走兜底逻辑。"""

    def __init__(self) -> None:
        self.api_key = settings.deepseek_api_key
        self.base_url = settings.deepseek_base_url
        self.model = settings.deepseek_model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def chat(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> str | None:
        if not self.available:
            return None
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
            )
            return response.choices[0].message.content
        except Exception:
            return None

    def json_chat(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        content = self.chat(messages, temperature=0.0)
        if not content:
            return None
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(content[start : end + 1])
                except json.JSONDecodeError:
                    return None
        return None

    def tool_call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str = "required",
    ) -> dict[str, Any] | None:
        if not self.available:
            return None
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=0.0,
            )
            message = response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None)
            if not tool_calls:
                return None

            tool_call = tool_calls[0]
            function = getattr(tool_call, "function", None)
            if function is None:
                return None

            arguments = getattr(function, "arguments", "") or "{}"
            try:
                parsed_args = json.loads(arguments)
            except json.JSONDecodeError:
                return None

            return {
                "id": getattr(tool_call, "id", ""),
                "name": getattr(function, "name", ""),
                "arguments": parsed_args,
            }
        except Exception:
            return None
