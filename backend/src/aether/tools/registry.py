"""Typed tool registry shared by the in-product assistant and the MCP server.

A tool is a thin, typed adapter over an existing API endpoint function, so the
assistant can never do more than the calling user could do through the REST API:
the same validation, the same audit records, the same row-level security. Every
invocation re-checks the caller's workspace role; the model never chooses the
workspace or the identity.

No tool can change a cloud. The strongest side effects are starting a read-only
discovery workflow and creating internal drafts (assessment runs, draft plans).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError
from temporalio.client import Client

from aether.api.deps import WorkspaceContext
from aether.auth.principal import Principal
from aether.config import Settings
from aether.core.enums import Role
from aether.core.errors import AetherError
from aether.db.session import workspace_scope
from aether.logging import get_logger

log = get_logger(__name__)


class SideEffect(StrEnum):
    READ = "read"
    READ_WORKFLOW = "read_workflow"  # starts a workflow that only reads from a cloud
    DRAFT = "draft"  # creates an internal record (assessment run, draft plan); never touches a cloud


@dataclass(frozen=True, slots=True)
class ToolContext:
    workspace_id: uuid.UUID
    principal: Principal
    settings: Settings
    temporal: Client | None
    request_id: str | None
    channel: str  # "assistant" or "mcp"


class Card(BaseModel):
    """What the UI renders for a tool result. Cards carry the unredacted data the
    user is entitled to see; the model receives a separate, compact projection."""

    kind: str
    title: str
    data: dict[str, Any]
    link: str | None = None


@dataclass(slots=True)
class ToolOutput:
    model: dict[str, Any]
    card: Card | None = None


@dataclass(slots=True)
class ToolOutcome:
    ok: bool
    model: dict[str, Any]
    card: Card | None
    error: str | None = None
    duration_ms: int = 0


Handler = Callable[[ToolContext, WorkspaceContext, Any], Awaitable[ToolOutput]]


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    title: str
    description: str
    input_model: type[BaseModel]
    min_role: Role
    side_effect: SideEffect
    handler: Handler = field(repr=False)

    def input_schema(self) -> dict[str, Any]:
        return inline_schema(self.input_model.model_json_schema())


_REGISTRY: dict[str, Tool] = {}


def tool(
    name: str,
    *,
    title: str,
    description: str,
    input_model: type[BaseModel],
    min_role: Role,
    side_effect: SideEffect = SideEffect.READ,
) -> Callable[[Handler], Handler]:
    if not name.replace("_", "").isalnum() or len(name) > 64:
        raise ValueError(f"invalid tool name {name!r}")  # provider tool-name rules: [a-zA-Z0-9_]

    def register(fn: Handler) -> Handler:
        if name in _REGISTRY:
            raise ValueError(f"duplicate tool {name!r}")
        _REGISTRY[name] = Tool(name, title, description, input_model, min_role, side_effect, fn)
        return fn

    return register


def all_tools() -> list[Tool]:
    _load()
    return sorted(_REGISTRY.values(), key=lambda t: t.name)


def get_tool(name: str) -> Tool | None:
    _load()
    return _REGISTRY.get(name)


def available_tools(principal: Principal, workspace_id: uuid.UUID) -> list[Tool]:
    """Tools the caller may use in this workspace, in a stable order (prompt caching)."""
    role = principal.role_in(workspace_id)
    if role is None:
        return []
    return [t for t in all_tools() if role.at_least(t.min_role)]


def _load() -> None:
    # Importing the module registers the built-in tools (kept lazy to avoid import cycles).
    from aether.tools import builtin  # noqa: F401


async def invoke(name: str, ctx: ToolContext, raw_args: dict[str, Any] | None) -> ToolOutcome:
    started = time.monotonic()

    def done(outcome: ToolOutcome) -> ToolOutcome:
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
        log.info(
            "tool.invoked",
            tool=name,
            channel=ctx.channel,
            ok=outcome.ok,
            duration_ms=outcome.duration_ms,
            workspace_id=str(ctx.workspace_id),
        )
        return outcome

    t = get_tool(name)
    if t is None:
        return done(ToolOutcome(False, {"error": f"unknown tool '{name}'"}, None, f"unknown tool '{name}'"))
    try:
        role = ctx.principal.require(ctx.workspace_id, t.min_role)
    except AetherError as e:
        return done(ToolOutcome(False, {"error": e.public_message}, None, e.public_message))
    try:
        args = t.input_model.model_validate(raw_args or {})
    except ValidationError as e:
        problems = [f"{'.'.join(str(p) for p in err['loc']) or 'input'}: {err['msg']}" for err in e.errors()]
        msg = "invalid arguments: " + "; ".join(problems[:5])
        return done(ToolOutcome(False, {"error": msg}, None, msg))
    try:
        async with workspace_scope(ctx.workspace_id) as session:
            wctx = WorkspaceContext(ctx.workspace_id, ctx.principal, role, session)
            out = await t.handler(ctx, wctx, args)
    except AetherError as e:
        return done(ToolOutcome(False, {"error": e.public_message}, None, e.public_message))
    except Exception:
        log.exception("tool.failed", tool=name)
        return done(ToolOutcome(False, {"error": "the tool failed unexpectedly"}, None, "internal error"))
    return done(ToolOutcome(True, out.model, out.card))


def inline_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve local ``$ref``s and drop ``title`` noise: small models handle flat schemas
    far better, and every provider accepts them."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = defs[node["$ref"].rsplit("/", 1)[-1]]
                merged = {**walk(target), **{k: walk(v) for k, v in node.items() if k != "$ref"}}
                return merged
            return {
                k: walk(v)
                for k, v in node.items()
                if k not in ("$defs", "title") or (k == "title" and not isinstance(v, str))
            }
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    out: dict[str, Any] = walk(schema)
    out.setdefault("type", "object")
    out.setdefault("properties", {})
    return out
