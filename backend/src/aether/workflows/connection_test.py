"""Connection test workflow.

The workflow carries only identifiers. The activity runs on the connector task
queue, the only workers that can read cloud credentials from OpenBao.
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy


class TestConnectionInput(BaseModel):
    workspace_id: str
    connection_id: str
    requested_by: str  # user id, for the audit trail
    requested_by_display: str | None = None
    request_id: str | None = None


TEST_CONNECTION_ACTIVITY = "test_connection"


@workflow.defn(name="TestConnection")
class TestConnectionWorkflow:
    @workflow.run
    async def run(self, inp: TestConnectionInput) -> dict[str, object]:
        result: dict[str, object] = await workflow.execute_activity(
            TEST_CONNECTION_ACTIVITY,
            inp,
            start_to_close_timeout=timedelta(seconds=90),
            # Connection tests are read-only and idempotent, but a failed test is a
            # result, not an error; only infrastructure failures are retried.
            retry_policy=RetryPolicy(maximum_attempts=3, non_retryable_error_types=["ValueError"]),
        )
        return result
