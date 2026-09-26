"""Model gateway: one streaming interface over the supported model providers.

Conversation messages are stored in a provider-neutral form::

    {"role": "user",      "blocks": [{"type": "text", "text": ...}]}
    {"role": "assistant", "blocks": [{"type": "text", ...}, {"type": "tool_call", "id", "name", "args"}],
                          "raw": [...provider-native content...], "provider": "anthropic"}
    {"role": "tool",      "blocks": [{"type": "tool_result", "call_id", "name", "content", "is_error"}]}

``raw`` preserves provider-specific blocks (thinking signatures, fallback markers) so a
conversation replays faithfully on the same provider; other providers use ``blocks``.
Model ids are configuration, never code.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic
import httpx

from aether.logging import get_logger

log = get_logger(__name__)

Message = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(slots=True)
class TextDelta:
    text: str


@dataclass(slots=True)
class Final:
    """End of one model response."""

    blocks: list[dict[str, Any]]
    raw: list[dict[str, Any]] | None
    stop_reason: str
    usage: Usage
    latency_ms: int
    model: str
    notice: str | None = None  # user-facing note, e.g. a refusal or a fallback model


ModelEvent = TextDelta | Final


class GatewayError(Exception):
    """A provider failure with a message that is safe to show to users."""


class Gateway(Protocol):
    provider: str
    model: str

    def stream(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> AsyncIterator[ModelEvent]: ...


def close_open_calls(messages: Sequence[Message]) -> list[Message]:
    """Every tool call must be followed by its result (providers reject dangling calls,
    which can remain after a crash mid-turn). Synthesize an error result for any gap."""
    messages = [dict(m) for m in messages]  # never mutate the caller's history
    out: list[Message] = []
    for i, m in enumerate(messages):
        out.append(m)
        if m["role"] != "assistant":
            continue
        calls = [b for b in m["blocks"] if b["type"] == "tool_call"]
        if not calls:
            continue
        nxt = messages[i + 1] if i + 1 < len(messages) else None
        answered = {b["call_id"] for b in nxt["blocks"]} if nxt and nxt["role"] == "tool" else set()
        missing = [c for c in calls if c["id"] not in answered]
        if missing:
            filler = [
                {
                    "type": "tool_result",
                    "call_id": c["id"],
                    "name": c["name"],
                    "content": "not executed",
                    "is_error": True,
                }
                for c in missing
            ]
            if nxt and nxt["role"] == "tool":
                nxt["blocks"] = list(nxt["blocks"]) + filler
            else:
                out.append({"role": "tool", "blocks": filler})
    return out


# ============================================================================ Anthropic


class AnthropicGateway:
    """Claude via the official SDK. Streams text; tool inputs are validated by the
    registry's Pydantic models before anything runs."""

    provider = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int,
        effort: str | None,
        fallbacks: bool,
        base_url: str | None = None,
        timeout_s: float = 120.0,
        http_client: Any = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.fallbacks = fallbacks
        self.client = anthropic.AsyncAnthropic(
            api_key=api_key, base_url=base_url, timeout=timeout_s, max_retries=2, http_client=http_client
        )

    async def aclose(self) -> None:
        await self.client.close()

    @staticmethod
    def to_wire(messages: Sequence[Message]) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = []
        for m in close_open_calls(messages):
            if m["role"] == "user":
                wire.append(
                    {"role": "user", "content": [{"type": "text", "text": b["text"]} for b in m["blocks"]]}
                )
            elif m["role"] == "assistant":
                if m.get("provider") == "anthropic" and m.get("raw"):
                    content = m["raw"]
                else:
                    content = []
                    for b in m["blocks"]:
                        if b["type"] == "text" and b["text"]:
                            content.append({"type": "text", "text": b["text"]})
                        elif b["type"] == "tool_call":
                            content.append(
                                {"type": "tool_use", "id": b["id"], "name": b["name"], "input": b["args"]}
                            )
                if content:
                    wire.append({"role": "assistant", "content": content})
            else:
                wire.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": b["call_id"],
                                "content": b["content"],
                                "is_error": bool(b.get("is_error")),
                            }
                            for b in m["blocks"]
                        ],
                    }
                )
        return wire

    async def stream(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> AsyncIterator[ModelEvent]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": system}],
            "messages": self.to_wire(messages),
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "eager_input_streaming": True,
                }
                for t in tools
            ],
            # Tools and system prompt are stable per role, so the prefix caches well.
            "cache_control": {"type": "ephemeral"},
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        if self.fallbacks:
            # On a safety-classifier decline, the API re-runs the request on Anthropic's
            # recommended model for that category instead of returning a refusal.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"

        started = time.monotonic()
        for attempt in range(3):
            try:
                async with self.client.beta.messages.stream(**kwargs) as s:
                    async for ev in s:
                        if ev.type == "text":
                            yield TextDelta(ev.text)
                    msg = await s.get_final_message()
                break
            except ValueError:
                # A tool input the SDK could not parse at all (eager input streaming).
                # No tool_use block completed, so re-issue the request (bounded).
                if attempt == 2:
                    raise GatewayError("the model produced malformed tool input") from None
                log.warning("anthropic.tool_json_retry", attempt=attempt + 1)
            except anthropic.AuthenticationError as e:
                raise GatewayError("the model provider rejected the API key") from e
            except anthropic.PermissionDeniedError as e:
                raise GatewayError("the API key is not allowed to use this model") from e
            except anthropic.NotFoundError as e:
                raise GatewayError(f"model '{self.model}' is not available") from e
            except anthropic.RateLimitError as e:
                raise GatewayError("the model provider is rate limiting requests; try again shortly") from e
            except anthropic.BadRequestError as e:
                log.warning("anthropic.bad_request", error=str(e)[:300])
                raise GatewayError("the model provider rejected the request") from e
            except anthropic.APIStatusError as e:
                raise GatewayError(f"the model provider returned an error ({e.status_code})") from e
            except anthropic.APIConnectionError as e:
                raise GatewayError("could not reach the model provider") from e

        blocks: list[dict[str, Any]] = []
        for b in msg.content:
            if b.type == "text":
                blocks.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                args = b.input if isinstance(b.input, dict) else {}
                blocks.append({"type": "tool_call", "id": b.id, "name": b.name, "args": args})
        notice = None
        stop = msg.stop_reason or "end_turn"
        if stop == "refusal":
            blocks = [b for b in blocks if b["type"] == "text"]  # never run tools from a refused turn
            notice = "The model declined this request."
        elif stop == "max_tokens" and any(b["type"] == "tool_call" for b in blocks):
            blocks = [b for b in blocks if b["type"] == "text"]  # a truncated tool input is not safe to run
            notice = "The response was cut off before a tool call completed."
        if msg.model != self.model:
            notice = f"Answered by fallback model {msg.model}."
        u = msg.usage
        yield Final(
            blocks=blocks,
            raw=[b.model_dump(mode="json", exclude_none=True) for b in msg.content],
            stop_reason=stop,
            usage=Usage(
                input_tokens=u.input_tokens or 0,
                output_tokens=u.output_tokens or 0,
                cache_read_tokens=u.cache_read_input_tokens or 0,
                cache_write_tokens=u.cache_creation_input_tokens or 0,
            ),
            latency_ms=int((time.monotonic() - started) * 1000),
            model=msg.model,
            notice=notice,
        )


# ============================================================================ Ollama (local)


class OllamaGateway:
    """A local model served by Ollama (``local_only`` workspaces). Uses Ollama's native
    chat API with tool support; nothing leaves the deployment."""

    provider = "ollama"

    def __init__(self, *, base_url: str, model: str, num_ctx: int = 16384, timeout_s: float = 300.0) -> None:
        self.model = model
        self.num_ctx = num_ctx
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=httpx.Timeout(timeout_s, connect=5.0)
        )

    @staticmethod
    def to_wire(system: str, messages: Sequence[Message]) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in close_open_calls(messages):
            if m["role"] == "user":
                wire.append({"role": "user", "content": "\n\n".join(b["text"] for b in m["blocks"])})
            elif m["role"] == "assistant":
                text = "".join(b["text"] for b in m["blocks"] if b["type"] == "text")
                calls = [
                    {"function": {"name": b["name"], "arguments": b["args"]}}
                    for b in m["blocks"]
                    if b["type"] == "tool_call"
                ]
                entry: dict[str, Any] = {"role": "assistant", "content": text}
                if calls:
                    entry["tool_calls"] = calls
                wire.append(entry)
            else:
                wire.extend(
                    {"role": "tool", "content": b["content"], "tool_name": b.get("name", "")}
                    for b in m["blocks"]
                )
        return wire

    async def stream(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> AsyncIterator[ModelEvent]:
        body = {
            "model": self.model,
            "messages": self.to_wire(system, messages),
            "tools": [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
                }
                for t in tools
            ],
            "stream": True,
            "options": {"temperature": 0.1, "num_ctx": self.num_ctx},
        }
        started = time.monotonic()
        text: list[str] = []
        calls: list[dict[str, Any]] = []
        last: dict[str, Any] = {}
        try:
            async with self.http.stream("POST", "/api/chat", json=body) as r:
                if r.status_code == 404:
                    raise GatewayError(f"local model '{self.model}' is not installed")
                if r.status_code >= 400:
                    raise GatewayError(f"the local model server returned an error ({r.status_code})")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise GatewayError("the local model failed: " + str(chunk["error"])[:200])
                    msg = chunk.get("message") or {}
                    if msg.get("content"):
                        text.append(msg["content"])
                        yield TextDelta(msg["content"])
                    for tc in msg.get("tool_calls") or []:
                        fn = tc.get("function") or {}
                        args = fn.get("arguments")
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {}
                        calls.append(
                            {
                                "type": "tool_call",
                                "id": f"call_{uuid.uuid4().hex[:12]}",
                                "name": fn.get("name", ""),
                                "args": args if isinstance(args, dict) else {},
                            }
                        )
                    if chunk.get("done"):
                        last = chunk
        except httpx.HTTPError as e:
            raise GatewayError("could not reach the local model server") from e

        blocks: list[dict[str, Any]] = []
        if text:
            blocks.append({"type": "text", "text": "".join(text)})
        blocks.extend(calls)
        yield Final(
            blocks=blocks,
            raw=None,
            stop_reason="tool_use" if calls else str(last.get("done_reason") or "end_turn"),
            usage=Usage(
                input_tokens=int(last.get("prompt_eval_count") or 0),
                output_tokens=int(last.get("eval_count") or 0),
            ),
            latency_ms=int((time.monotonic() - started) * 1000),
            model=self.model,
        )

    async def aclose(self) -> None:
        await self.http.aclose()


# ============================================================================ Scripted (tests, offline evals)


@dataclass
class ScriptedGateway:
    """Replays a fixed list of responses. Each entry is either a string (final text) or
    a list of blocks. Records every request it receives for assertions."""

    responses: list[Any]
    provider: str = "scripted"
    model: str = "scripted-1"
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def stream(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> AsyncIterator[ModelEvent]:
        self.requests.append(
            {"system": system, "messages": close_open_calls(messages), "tools": [t.name for t in tools]}
        )
        if not self.responses:
            raise GatewayError("script exhausted")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if callable(nxt):
            nxt = nxt(messages)
        blocks = [{"type": "text", "text": nxt}] if isinstance(nxt, str) else list(nxt)
        for b in blocks:
            if b["type"] == "text":
                for i in range(0, len(b["text"]), 7):
                    yield TextDelta(b["text"][i : i + 7])
        has_calls = any(b["type"] == "tool_call" for b in blocks)
        yield Final(
            blocks=blocks,
            raw=None,
            stop_reason="tool_use" if has_calls else "end_turn",
            usage=Usage(input_tokens=100, output_tokens=20),
            latency_ms=1,
            model=self.model,
        )
