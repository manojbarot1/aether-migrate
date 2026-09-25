"""IAC models for AETHER MIGRATE."""

from __future__ import annotations

from core.models import ProviderName
from pydantic import BaseModel


class TofuVariable(BaseModel):
    description: str
    type: str           # "string", "number", "bool", "list(string)"
    default: str | None = None
    sensitive: bool = False


class TofuModule(BaseModel):
    provider: ProviderName
    region: str
    files: dict[str, str]           # filename → HCL content
    variables: dict[str, TofuVariable]
    outputs: dict[str, str]


class TofuValidationResult(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]
