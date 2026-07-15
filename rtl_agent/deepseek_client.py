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
        on_delta: Callable[[str, str], None] | None = None,
    ) -> Any:
        """Send one chat completion request and return the assistant message.

        When ``on_delta`` is provided the request is streamed and the callback is
        invoked with ``(kind, delta_text)`` as tokens arrive -- ``kind`` is one of
        ``"reasoning"`` (thinking), ``"content"`` (final answer), or ``"tool"`` (a
        tool name once it appears). This drives the live status bar.

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

        if on_delta is not None:
            return self._chat_streaming(kwargs, messages, tools, on_delta)

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

    def _chat_streaming(self, kwargs, messages, tools, on_delta) -> Any:
        kwargs = {**kwargs, "stream": True}
        try:
            stream = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise DeepSeekError(str(exc)) from exc

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_slots: dict[int, dict] = {}
        seen_tool_names: set[str] = set()
        finish_reason = None
        request_id = None
        usage = None

        try:
            for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                if getattr(chunk, "id", None):
                    request_id = chunk.id
                if not getattr(chunk, "choices", None):
                    continue
                choice = chunk.choices[0]
                delta = choice.delta

                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    reasoning_parts.append(rc)
                    on_delta("reasoning", rc)
                if getattr(delta, "content", None):
                    content_parts.append(delta.content)
                    on_delta("content", delta.content)
                for tc in (getattr(delta, "tool_calls", None) or []):
                    slot = tool_slots.setdefault(tc.index, {"id": None, "name": "", "args": ""})
                    if getattr(tc, "id", None):
                        slot["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn:
                        if getattr(fn, "name", None):
                            slot["name"] = fn.name
                        if getattr(fn, "arguments", None):
                            slot["args"] += fn.arguments
                    if slot["name"] and slot["name"] not in seen_tool_names:
                        seen_tool_names.add(slot["name"])
                        on_delta("tool", slot["name"])
                if getattr(choice, "finish_reason", None):
                    finish_reason = choice.finish_reason
        except Exception as exc:  # noqa: BLE001
            raise DeepSeekError(str(exc)) from exc

        tool_calls = [
            _ToolCall(tool_slots[i]["id"], tool_slots[i]["name"], tool_slots[i]["args"])
            for i in sorted(tool_slots)
        ]
        message = _StreamedMessage(
            content="".join(content_parts) or None,
            reasoning_content="".join(reasoning_parts) or None,
            tool_calls=tool_calls or None,
        )

        if self._on_call is not None:
            self._on_call(
                {
                    "model": DEEPSEEK_MODEL,
                    "request_messages": messages,
                    "tools": tools,
                    "finish_reason": finish_reason,
                    "reasoning_content": message.reasoning_content,
                    "content": message.content,
                    "tool_calls": _serialize_tool_calls(message),
                    "usage": _usage_dict(usage),
                    "request_id": request_id,
                    "streamed": True,
                }
            )

        return message

    def simple_completion(
        self, messages: list[dict], timeout: float = 45.0, max_tokens: int = 160
    ) -> str:
        """A lightweight, non-thinking completion for auxiliary tasks (summaries).

        Deliberately omits thinking mode and reasoning_effort so it is fast and
        cheap. Each call is independent -- callers pass exactly the messages they
        want and no history is retained.
        """
        try:
            response = self._client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=messages,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise DeepSeekError(str(exc)) from exc
        return response.choices[0].message.content or ""

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
    return _usage_dict(getattr(response, "usage", None))


def _usage_dict(usage: Any) -> dict | None:
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


class _Fn:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, id: str | None, name: str, arguments: str):
        self.id = id
        self.type = "function"
        self.function = _Fn(name, arguments)


class _StreamedMessage:
    """Reassembled assistant message, shaped like the non-streaming SDK object."""

    def __init__(self, content, reasoning_content, tool_calls):
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls
