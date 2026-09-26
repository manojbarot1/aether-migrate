"""Effective assistant settings, provider availability and token budgets."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aether.assistant.gateway import AnthropicGateway, Gateway, OllamaGateway
from aether.config import Settings
from aether.db.models import AssistantSettings, LlmCall

EGRESS_MODES = ("external_allowed", "external_redacted", "local_only")


@dataclass(frozen=True, slots=True)
class EffectiveSettings:
    enabled: bool
    provider: str
    model: str
    egress_mode: str
    monthly_token_budget: int | None
    configured: bool  # a workspace row exists (otherwise platform defaults apply)

    @property
    def redact(self) -> bool:
        return self.egress_mode == "external_redacted"


def provider_status(settings: Settings) -> dict[str, dict[str, object]]:
    """Which providers this deployment can use, and the models on offer."""
    key = settings.secret(settings.anthropic_api_key_file)
    has_key = key is not None and bool(key.get_secret_value())
    ollama_models = settings.ollama_models or [settings.ollama_model]
    return {
        "anthropic": {
            "available": has_key,
            "reason": None if has_key else "no Anthropic API key is configured for this deployment",
            "models": settings.assistant_models,
            "external": True,
        },
        "ollama": {
            "available": bool(settings.ollama_url),
            "reason": None if settings.ollama_url else "no local model server is configured",
            "models": ollama_models,
            "external": False,
        },
    }


async def effective_settings(
    session: AsyncSession, settings: Settings, workspace_id: uuid.UUID
) -> EffectiveSettings:
    row = await session.get(AssistantSettings, workspace_id)
    if row is not None:
        return EffectiveSettings(
            row.enabled, row.provider, row.model, row.egress_mode, row.monthly_token_budget, configured=True
        )
    provider = settings.assistant_provider
    egress = settings.assistant_default_egress
    if egress == "local_only":
        provider = "ollama"
    model = settings.ollama_model if provider == "ollama" else settings.assistant_model
    return EffectiveSettings(
        True, provider, model, egress, settings.assistant_default_monthly_tokens, configured=False
    )


def unavailable_reason(settings: Settings, eff: EffectiveSettings) -> str | None:
    if not eff.enabled:
        return "the assistant is disabled for this workspace"
    if eff.egress_mode == "local_only" and eff.provider != "ollama":
        return "this workspace only allows a local model"
    status = provider_status(settings).get(eff.provider)
    if status is None:
        return f"unknown provider '{eff.provider}'"
    if not status["available"]:
        return str(status["reason"])
    return None


def make_gateway(settings: Settings, eff: EffectiveSettings) -> Gateway:
    if eff.provider == "anthropic":
        key = settings.secret(settings.anthropic_api_key_file)
        assert key is not None
        return AnthropicGateway(
            api_key=key.get_secret_value(),
            model=eff.model,
            max_tokens=settings.assistant_max_tokens,
            effort=settings.assistant_effort,
            fallbacks=eff.model in settings.assistant_fallback_models,
            base_url=settings.anthropic_base_url,
        )
    if eff.provider == "ollama":
        assert settings.ollama_url
        return OllamaGateway(
            base_url=settings.ollama_url, model=eff.model, timeout_s=settings.ollama_timeout_s
        )
    raise ValueError(f"unsupported provider {eff.provider}")


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def tokens_this_month(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    used = (
        await session.execute(
            select(func.coalesce(func.sum(LlmCall.input_tokens + LlmCall.output_tokens), 0)).where(
                LlmCall.workspace_id == workspace_id, LlmCall.created_at >= month_start()
            )
        )
    ).scalar_one()
    return int(used)
