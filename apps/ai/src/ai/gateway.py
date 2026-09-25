"""ModelGateway — abstraction over multiple LLM backends.

Supported providers (via pydantic-ai):
- openai    — OpenAI API
- anthropic — Anthropic Claude API
- ollama    — local Ollama instance
- openrouter — OpenAI-compatible OpenRouter endpoint

API keys are read from ``/run/secrets/llm_api_key`` (Docker secrets) or the
``LLM_API_KEY`` environment variable.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ChatMessage:
    """A single message in a conversation."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None


@dataclass
class ModelConfig:
    """Configuration for a single LLM call."""

    provider: Literal["openai", "anthropic", "ollama", "openrouter"] = "openai"
    model_id: str = "gpt-4o"
    max_tokens: int = 4096
    temperature: float = 0.1
    ollama_base_url: str = "http://localhost:11434"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"


@dataclass
class ChatChunk:
    """A streaming chunk from the model."""

    type: Literal["text", "tool_call", "tool_result", "done", "error"]
    content: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    result_id: str | None = None
    card_type: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class ToolSpec:
    """Minimal tool description passed to the LLM."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


# ---------------------------------------------------------------------------
# API key loading
# ---------------------------------------------------------------------------


def _load_api_key() -> str | None:
    """Read the LLM API key from Docker secrets or environment variable."""
    secret_path = "/run/secrets/llm_api_key"
    if os.path.exists(secret_path):
        try:
            return open(secret_path).read().strip()
        except OSError:
            pass
    return os.environ.get("LLM_API_KEY")


# ---------------------------------------------------------------------------
# ModelGateway
# ---------------------------------------------------------------------------


class ModelGateway:
    """Abstraction over multiple LLM backends.

    This implementation uses ``pydantic-ai`` when available; it falls back to
    a direct httpx approach for providers not yet wrapped by pydantic-ai.

    Tool invocation is handled by the ``AIOrchestrator`` — the gateway only
    streams tokens and notifies callers when the model requests a tool call.
    """

    def __init__(self) -> None:
        self._api_key = _load_api_key()

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        model_config: ModelConfig,
    ) -> AsyncIterator[ChatChunk]:
        """Stream a chat completion.

        Yields ``ChatChunk`` objects. When the model requests a tool call the
        caller receives a ``type="tool_call"`` chunk; it must then execute the
        tool and call ``chat_stream`` again with the tool result appended.
        """
        t_start = time.monotonic()
        try:
            async for chunk in self._dispatch(messages, tools, model_config):
                yield chunk
        finally:
            latency_ms = int((time.monotonic() - t_start) * 1000)
            log.info(
                "llm_call_complete",
                provider=model_config.provider,
                model=model_config.model_id,
                latency_ms=latency_ms,
            )

    async def _dispatch(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        model_config: ModelConfig,
    ) -> AsyncIterator[ChatChunk]:
        provider = model_config.provider
        if provider == "openai":
            async for chunk in self._openai_stream(messages, tools, model_config):
                yield chunk
        elif provider == "anthropic":
            async for chunk in self._anthropic_stream(messages, tools, model_config):
                yield chunk
        elif provider == "ollama":
            async for chunk in self._ollama_stream(messages, tools, model_config):
                yield chunk
        elif provider == "openrouter":
            async for chunk in self._openrouter_stream(messages, tools, model_config):
                yield chunk
        else:
            yield ChatChunk(
                type="error",
                error_code="unsupported_provider",
                error_message=f"Provider '{provider}' is not supported",
            )

    # ------------------------------------------------------------------
    # Provider implementations
    # ------------------------------------------------------------------

    async def _openai_stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        config: ModelConfig,
    ) -> AsyncIterator[ChatChunk]:
        """Stream via pydantic-ai OpenAI provider."""
        try:
            from pydantic_ai import Agent
            from pydantic_ai.models.openai import OpenAIModel
        except ImportError:
            yield ChatChunk(
                type="error",
                error_code="missing_dependency",
                error_message="pydantic-ai is not installed",
            )
            return

        model = OpenAIModel(config.model_id, api_key=self._api_key)
        agent: Agent[None, str] = Agent(model)  # type: ignore[type-arg]
        prompt = _messages_to_prompt(messages)

        async with agent.run_stream(prompt) as result:
            async for text in result.stream():
                yield ChatChunk(type="text", content=text)

    async def _anthropic_stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        config: ModelConfig,
    ) -> AsyncIterator[ChatChunk]:
        try:
            from pydantic_ai import Agent
            from pydantic_ai.models.anthropic import AnthropicModel
        except ImportError:
            yield ChatChunk(
                type="error",
                error_code="missing_dependency",
                error_message="pydantic-ai anthropic provider is not installed",
            )
            return

        model = AnthropicModel(config.model_id, api_key=self._api_key)
        agent: Agent[None, str] = Agent(model)  # type: ignore[type-arg]
        prompt = _messages_to_prompt(messages)

        async with agent.run_stream(prompt) as result:
            async for text in result.stream():
                yield ChatChunk(type="text", content=text)

    async def _ollama_stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        config: ModelConfig,
    ) -> AsyncIterator[ChatChunk]:
        """Stream via pydantic-ai Ollama provider."""
        try:
            from pydantic_ai import Agent
            from pydantic_ai.models.ollama import OllamaModel
        except ImportError:
            yield ChatChunk(
                type="error",
                error_code="missing_dependency",
                error_message="pydantic-ai ollama provider is not installed",
            )
            return

        model = OllamaModel(
            config.model_id,
            base_url=config.ollama_base_url,
        )
        agent: Agent[None, str] = Agent(model)  # type: ignore[type-arg]
        prompt = _messages_to_prompt(messages)

        async with agent.run_stream(prompt) as result:
            async for text in result.stream():
                yield ChatChunk(type="text", content=text)

    async def _openrouter_stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        config: ModelConfig,
    ) -> AsyncIterator[ChatChunk]:
        """Stream via OpenAI-compatible OpenRouter endpoint using httpx."""
        import json

        import httpx

        payload: dict[str, Any] = {
            "model": config.model_id,
            "messages": [_msg_to_openai_dict(m) for m in messages],
            "stream": True,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
        }
        if tools:
            payload["tools"] = [_tool_spec_to_openai(t) for t in tools]
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{config.openrouter_base_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    yield ChatChunk(
                        type="error",
                        error_code="provider_error",
                        error_message=f"OpenRouter returned {response.status_code}: {body[:200]}",
                    )
                    return

                pending_tool: dict[str, Any] | None = None
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    choice = event.get("choices", [{}])[0]
                    delta = choice.get("delta", {})

                    # Text delta
                    if delta.get("content"):
                        yield ChatChunk(type="text", content=delta["content"])

                    # Tool call delta
                    tool_calls = delta.get("tool_calls", [])
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        if pending_tool is None:
                            pending_tool = {
                                "id": tc.get("id", ""),
                                "name": fn.get("name", ""),
                                "arguments": fn.get("arguments", ""),
                            }
                        else:
                            pending_tool["arguments"] += fn.get("arguments", "")

                    finish = choice.get("finish_reason")
                    if finish == "tool_calls" and pending_tool:
                        try:
                            args = json.loads(pending_tool["arguments"])
                        except json.JSONDecodeError:
                            args = {}
                        yield ChatChunk(
                            type="tool_call",
                            tool_name=pending_tool["name"],
                            tool_input=args,
                        )
                        pending_tool = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _messages_to_prompt(messages: list[ChatMessage]) -> str:
    """Concatenate messages into a single prompt string for simple agents."""
    parts = []
    for m in messages:
        if m.role == "system":
            parts.append(f"[System]\n{m.content}")
        elif m.role == "user":
            parts.append(f"[User]\n{m.content}")
        elif m.role == "assistant":
            parts.append(f"[Assistant]\n{m.content}")
        elif m.role == "tool":
            parts.append(f"[Tool result: {m.tool_name}]\n{m.content}")
    return "\n\n".join(parts)


def _msg_to_openai_dict(m: ChatMessage) -> dict[str, Any]:
    d: dict[str, Any] = {"role": m.role, "content": m.content or ""}
    if m.tool_call_id:
        d["tool_call_id"] = m.tool_call_id
    return d


def _tool_spec_to_openai(t: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        },
    }
