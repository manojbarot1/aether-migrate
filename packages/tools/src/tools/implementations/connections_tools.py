"""connections.list — list connections for the workspace (metadata only)."""

from __future__ import annotations

from typing import Any

from db.models import ConnectionRow
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tools.registry import CurrentUser, ToolDefinition

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ConnectionsListInput(BaseModel):
    workspace_id: str


class ConnectionSummary(BaseModel):
    id: str
    name: str
    provider: str
    mode: str
    last_test_ok: bool | None
    last_tested_at: str | None


class ConnectionsListOutput(BaseModel):
    connections: list[ConnectionSummary]


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

CONNECTIONS_LIST_TOOL = ToolDefinition(
    name="connections.list",
    description=(
        "List all cloud connections configured for the workspace. "
        "Returns metadata only — no secrets or credentials."
    ),
    input_schema=ConnectionsListInput,
    output_schema=ConnectionsListOutput,
    side_effect_class="read",
    required_role="viewer",
    tags=["connections"],
)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def connections_list_handler(
    input_data: dict[str, Any],
    user: CurrentUser,
    db: AsyncSession,
) -> dict[str, Any]:
    result = await db.execute(
        select(ConnectionRow).where(
            ConnectionRow.workspace_id == user.workspace_id  # type: ignore[arg-type]
        )
    )
    rows = result.scalars().all()

    connections = [
        ConnectionSummary(
            id=str(row.id),
            name=row.name,
            provider=row.provider,
            mode=row.mode,
            last_test_ok=row.last_test_ok,
            last_tested_at=(
                row.last_tested_at.isoformat() if row.last_tested_at else None
            ),
        )
        for row in rows
    ]
    return ConnectionsListOutput(connections=connections).model_dump()
