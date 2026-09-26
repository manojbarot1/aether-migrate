"""Runtime configuration.

Plain settings come from environment variables (prefix ``AETHER_``). Secrets come
from files mounted by Docker secrets under ``/run/secrets`` so they never appear in
``docker inspect`` output or process environments.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SECRETS_DIR = Path("/run/secrets")


def read_secret_file(name: str, secrets_dir: Path = SECRETS_DIR) -> SecretStr | None:
    path = secrets_dir / name
    if not path.is_file():
        return None
    return SecretStr(path.read_text().strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AETHER_", extra="ignore")

    env: Literal["dev", "test", "prod"] = "prod"
    service_name: str = "aether-api"
    version: str = "0.1.0"
    log_level: str = "INFO"
    log_json: bool = True

    public_url: str = "https://localhost"

    # Database: runtime role is subject to row-level security; the owner role is used
    # only by the migration job.
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "aether"
    db_user: str = "aether_app"
    db_password: SecretStr | None = None
    db_password_file: str = "pg_app_password"
    db_pool_size: int = 10

    # OIDC
    oidc_issuer: str = "https://localhost/auth/realms/aether"
    oidc_internal_base: str = "http://keycloak:8080/auth/realms/aether"
    oidc_audience: str = "aether-api"
    oidc_client_id: str = "aether-web"
    oidc_admin_role: str = "platform-admin"

    # Temporal
    temporal_address: str = "temporal:7233"
    temporal_namespace: str = "aether"
    connector_task_queue: str = "connector"
    domain_task_queue: str = "domain"

    # OpenBao
    bao_addr: str = "http://openbao:8200"
    bao_kv_mount: str = "cloud-creds"
    bao_role_id_file: str = "bao_role_id"
    bao_secret_id_file: str = "bao_secret_id"

    # The platform's own AWS identity that customers trust in their role's trust policy.
    aws_platform_principal_arn: str | None = None

    # Catalog: target regions priced by the daily sync, and the default display currency.
    catalog_azure_regions: list[str] = Field(
        default_factory=lambda: [
            "westeurope",
            "northeurope",
            "germanywestcentral",
            "eastus",
            "eastus2",
            "uksouth",
        ]
    )
    default_currency: str = "EUR"

    # Assistant (runs in the separate `assistant` service, the only one with LLM egress).
    # Platform defaults; workspace admins choose provider/model/egress within these.
    assistant_provider: Literal["anthropic", "ollama"] = "anthropic"
    assistant_model: str = "claude-opus-5"
    assistant_models: list[str] = Field(
        default_factory=lambda: ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
        description="Anthropic models a workspace may select",
    )
    assistant_default_egress: Literal["external_allowed", "external_redacted", "local_only"] = (
        "external_redacted"
    )
    assistant_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = "medium"
    assistant_fallback_models: list[str] = Field(
        default_factory=lambda: ["claude-opus-5", "claude-fable-5-1", "claude-fable-5"],
        description="models for which server-side refusal fallbacks are requested",
    )
    assistant_max_tokens: int = 16000
    assistant_max_steps: int = 8
    assistant_retention_days: int = 30
    assistant_default_monthly_tokens: int | None = None
    anthropic_api_key_file: str = "anthropic_api_key"
    anthropic_base_url: str | None = None
    ollama_url: str | None = None
    ollama_model: str = "qwen2.5:7b"
    # CPU-only hosts can take minutes to process a long prompt before the first token.
    ollama_timeout_s: float = 900.0
    ollama_models: list[str] = Field(default_factory=list, description="local models a workspace may select")

    # Observability
    otel_endpoint: str | None = Field(
        default=None, description="OTLP gRPC endpoint, e.g. http://otel-collector:4317"
    )

    secrets_dir: Path = SECRETS_DIR

    def secret(self, filename: str) -> SecretStr | None:
        return read_secret_file(filename, self.secrets_dir)

    @property
    def database_url(self) -> str:
        password = self.db_password or self.secret(self.db_password_file)
        pw = quote(password.get_secret_value(), safe="") if password else ""
        return f"postgresql+asyncpg://{self.db_user}:{pw}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def oidc_jwks_url(self) -> str:
        return f"{self.oidc_internal_base}/protocol/openid-connect/certs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
