from __future__ import annotations

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from aether.config import Settings


async def connect(settings: Settings) -> Client:
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        data_converter=pydantic_data_converter,
    )
