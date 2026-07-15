"""DeepSeek-V4-Pro client wrapper.

Uses the OpenAI-compatible Chat Completions API in thinking mode. Preserves the
complete assistant message on tool-call turns -- ``reasoning_content``, ``content``
and ``tool_calls`` -- and passes ``reasoning_content`` back in subsequent requests
for the same interaction, as required by DeepSeek thinking mode.
"""

from __future__ import annotations

from typing import Any, Callable

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-pro"
DEEPSEEK_REASONING_EFFORT = "max"
DEEPSEEK_THINKING = {"type": "enabled"}


class DeepSeekError(RuntimeError):
    """Raised when a DeepSeek API request fails."""


class DeepSeekClient:
    def __init__(self, api_key: str, on_call: Callable[[dict], None] | None = None):
        # Import lazily so the rest of the app can load without the SDK present.
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
        self._on_call = on_call

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        timeout: float = 120.0,
    ) -> Any:
        """Send one chat completion request and return the assistant message.

        Do not send sampling controls (e.g. temperature) with thinking enabled.
        """
        kwargs: dict[str, Any] = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "reasoning_effort": DEEPSEEK_REASONING_EFFORT,
            "extra_body": {"thinking": DEEPSEEK_THINKING},
            "timeout": timeout,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surface any SDK/network error uniformly
            raise DeepSeekError(str(exc)) from exc

        message = response.choices[0].message

        if self._on_call is not None:
            self._on_call(
                {
                    "model": DEEPSEEK_MODEL,
                    "request_messages": messages,
                    "tools": tools,
                    "finish_reason": response.choices[0].finish_reason,
                    "reasoning_content": getattr(message, "reasoning_content", None),
                    "content": message.content,
                    "tool_calls": _serialize_tool_calls(message),
                    "usage": _serialize_usage(response),
                    "request_id": getattr(response, "id", None),
                }
            )

        return message

    def validate_key(self, timeout: float = 60.0) -> bool:
        """Minimal request used by startup preflight to validate the API key."""
        message = self.chat(
            messages=[
                {
                    "role": "user",
                    "content": "Return exactly: RTL_AGENT_API_READY",
                }
            ],
            timeout=timeout,
        )
        return "RTL_AGENT_API_READY" in (message.content or "")


def assistant_message_to_dict(message: Any) -> dict:
    """Serialize a DeepSeek assistant message for appending to history.

    Preserves reasoning_content, content and tool_calls so the required thinking
    context is passed back in later requests for the same interaction.
    """
    out: dict[str, Any] = {"role": "assistant"}
    content = getattr(message, "content", None)
    out["content"] = content if content is not None else ""

    reasoning = getattr(message, "reasoning_content", None)
    if reasoning:
        out["reasoning_content"] = reasoning

    tool_calls = _serialize_tool_calls(message)
    if tool_calls:
        out["tool_calls"] = tool_calls

    return out


def _serialize_tool_calls(message: Any) -> list[dict] | None:
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        return None
    serialized = []
    for call in tool_calls:
        serialized.append(
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
        )
    return serialized


def _serialize_usage(response: Any) -> dict | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    try:
        return usage.model_dump()
    except AttributeError:
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
