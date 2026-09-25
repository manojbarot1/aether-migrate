"""Typed tool registry for AETHER MIGRATE.

Tools are the primitive operations that the AI orchestrator (Phase 3) and
MCP server (Phase 7) can invoke. Each tool is a named, schema-validated
operation with an explicit side-effect class and required RBAC role.

Usage::

    from tools.registry import registry

    registry.register(ToolDefinition(
        name="inventory.search_vms",
        description="Search VMs with filters.",
        input_schema=SearchVMsInput,
        output_schema=SearchVMsOutput,
        side_effect_class="read",
        required_role="viewer",
    ))

    await registry.execute("inventory.search_vms", input_data, user, db)
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Literal

from core.errors import AuthorizationError
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

SideEffectClass = Literal["read", "read-workflow", "draft", "mutate"]

# Role hierarchy — higher index = more permissive. Viewer is the least privileged.
_ROLE_HIERARCHY: list[str] = [
    "viewer",
    "analyst",
    "connection-admin",
    "approver",
    "operator",
    "admin",
]


def _role_rank(role: str) -> int:
    """Return the hierarchy rank of *role* (higher = more privileged)."""
    try:
        return _ROLE_HIERARCHY.index(role)
    except ValueError:
        return -1


class CurrentUser:
    """Minimal representation of the authenticated caller."""

    def __init__(self, user_id: str, email: str, role: str, workspace_id: str) -> None:
        self.user_id = user_id
        self.email = email
        self.role = role
        self.workspace_id = workspace_id

    def has_role(self, required_role: str) -> bool:
        """Return True if this user's role is >= *required_role* in the hierarchy."""
        return _role_rank(self.role) >= _role_rank(required_role)


@dataclass
class ToolDefinition:
    """Descriptor for a single registered tool.

    Attributes:
        name:              Unique tool name (e.g. ``"inventory.search_vms"``).
        description:       Human-readable description.
        input_schema:      Pydantic v2 model class that validates inputs.
        output_schema:     Pydantic v2 model class that validates outputs.
        side_effect_class: Mutation level — ``"read"``, ``"read-workflow"``,
                           ``"draft"``, or ``"mutate"``.  Mutating tools are
                           never returned to the AI and cannot be invoked.
        required_role:     Minimum RBAC role needed to invoke this tool.
        tags:              Optional grouping tags.
    """

    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    side_effect_class: SideEffectClass
    required_role: str = "analyst"
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ToolDefinition.name must not be empty")
        if self.side_effect_class not in ("read", "read-workflow", "draft", "mutate"):
            raise ValueError(f"Invalid side_effect_class: {self.side_effect_class!r}")


# Handler type alias: async fn(input_data: dict, user: CurrentUser, db: AsyncSession) -> dict
ToolHandler = Callable[
    [dict[str, Any], "CurrentUser", AsyncSession],
    Coroutine[Any, Any, dict[str, Any]],
]


class ToolRegistry:
    """Singleton registry of all available AETHER MIGRATE tools.

    Mutating tools (``side_effect_class="mutate"``) are accepted at
    registration time but are silently excluded from ``list_for_role`` and
    will raise ``AuthorizationError`` in ``execute``.  This guarantees the AI
    orchestrator can never invoke a destructive operation.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, ToolHandler] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, tool: ToolDefinition) -> None:
        """Register *tool*. Raises ``ValueError`` if the name is already taken."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def register_handler(self, name: str, handler: ToolHandler) -> None:
        """Associate *handler* with the tool identified by *name*."""
        if name not in self._tools:
            raise KeyError(f"No tool registered with name '{name}'")
        self._handlers[name] = handler

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> ToolDefinition | None:
        """Return the tool registered under *name*, or ``None`` if not found."""
        return self._tools.get(name)

    def list_for_role(self, role: str) -> list[ToolDefinition]:
        """Return tools accessible to *role*.

        Rules enforced:
        - Mutating tools (side_effect_class="mutate") are NEVER returned.
        - The caller's role must satisfy the tool's ``required_role`` using
          the role hierarchy (viewer < analyst < connection-admin < approver
          < operator < admin).
        """
        caller_rank = _role_rank(role)
        return [
            t
            for t in self._tools.values()
            if t.side_effect_class != "mutate"
            and caller_rank >= _role_rank(t.required_role)
        ]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        name: str,
        input_data: dict[str, Any],
        user: CurrentUser,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Validate authorization and invoke the handler for *name*.

        Raises:
            KeyError: if no tool with *name* is registered.
            AuthorizationError: if the user lacks sufficient role or the tool
                is a mutating tool.
            RuntimeError: if no handler is registered for the tool.
        """
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"No tool registered with name '{name}'")

        # Mutating tools are always blocked — not even admins can call them via AI
        if tool.side_effect_class == "mutate":
            raise AuthorizationError(
                action=name,
                required_role=tool.required_role,
            )

        if not user.has_role(tool.required_role):
            raise AuthorizationError(
                action=name,
                required_role=tool.required_role,
            )

        handler = self._handlers.get(name)
        if handler is None:
            raise RuntimeError(f"No handler registered for tool '{name}'")

        log.info("tool_execute", extra={"tool": name, "user_id": user.user_id})
        return await handler(input_data, user, db)

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools


# Module-level singleton — import and use directly
registry = ToolRegistry()
