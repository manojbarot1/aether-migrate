"""Tool loader — registers all Phase 3 tool definitions and handlers.

Call ``load_all_tools(registry, db_session_factory)`` once at application
startup (e.g. in FastAPI's ``lifespan``).

``migration.execute`` is intentionally NEVER registered here.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tools.implementations.assessment_tools import (
    ASSESSMENT_RUN_TOOL,
    assessment_run_handler,
)
from tools.implementations.connections_tools import (
    CONNECTIONS_LIST_TOOL,
    connections_list_handler,
)
from tools.implementations.cost_tools import (
    COST_COMPARE_TOOL,
    cost_compare_handler,
)
from tools.implementations.discovery_tools import (
    DISCOVERY_REFRESH_TOOL,
    DISCOVERY_STATUS_TOOL,
    discovery_refresh_handler,
    discovery_status_handler,
)
from tools.implementations.inventory_tools import (
    INVENTORY_DIFF_SNAPSHOTS_TOOL,
    INVENTORY_GET_RESOURCE_TOOL,
    INVENTORY_SEARCH_VMS_TOOL,
    inventory_diff_snapshots_handler,
    inventory_get_resource_handler,
    inventory_search_vms_handler,
)
from tools.implementations.plan_tools import (
    PLAN_CREATE_TOOL,
    PLAN_EXPLAIN_TOOL,
    PLAN_GET_TOOL,
    plan_create_handler,
    plan_explain_handler,
    plan_get_handler,
)
from tools.implementations.sizing_tools import (
    SIZING_RECOMMEND_TOOL,
    sizing_recommend_handler,
)
from tools.implementations.topology_tools import (
    TOPOLOGY_GET_TOOL,
    topology_get_handler,
)
from tools.registry import ToolRegistry

# Type alias for handler callables
_Handler = Callable[
    [dict[str, Any], Any, AsyncSession],
    Coroutine[Any, Any, dict[str, Any]],
]

# All tools to register: (ToolDefinition, handler)
_ALL_TOOLS: list[tuple[Any, _Handler]] = [
    (CONNECTIONS_LIST_TOOL, connections_list_handler),
    (DISCOVERY_STATUS_TOOL, discovery_status_handler),
    (DISCOVERY_REFRESH_TOOL, discovery_refresh_handler),
    (INVENTORY_SEARCH_VMS_TOOL, inventory_search_vms_handler),
    (INVENTORY_GET_RESOURCE_TOOL, inventory_get_resource_handler),
    (INVENTORY_DIFF_SNAPSHOTS_TOOL, inventory_diff_snapshots_handler),
    (TOPOLOGY_GET_TOOL, topology_get_handler),
    (SIZING_RECOMMEND_TOOL, sizing_recommend_handler),
    (COST_COMPARE_TOOL, cost_compare_handler),
    (ASSESSMENT_RUN_TOOL, assessment_run_handler),
    (PLAN_CREATE_TOOL, plan_create_handler),
    (PLAN_GET_TOOL, plan_get_handler),
    (PLAN_EXPLAIN_TOOL, plan_explain_handler),
]


def load_all_tools(
    reg: ToolRegistry,
    db_session_factory: Any = None,  # noqa: ANN401  — reserved for future DI
) -> None:
    """Register all v1.0 tools and their handlers into *reg*.

    Parameters
    ----------
    reg:
        The ``ToolRegistry`` instance to populate.
    db_session_factory:
        Reserved for future use — handlers receive ``db`` from the
        orchestrator at call time; the factory is not needed at registration.
    """
    for tool_def, handler in _ALL_TOOLS:
        if tool_def.name not in reg:
            reg.register(tool_def)
            reg.register_handler(tool_def.name, handler)
