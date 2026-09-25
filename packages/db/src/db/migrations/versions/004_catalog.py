"""Migration 004 — catalog tables for instance types, prices, FX rates, and disk prices.

Revision ID: 004
Revises: 003
Create Date: 2025-01-01 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # catalog_instance_types
    # ------------------------------------------------------------------
    op.create_table(
        "catalog_instance_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("catalog_version", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=False), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("vcpu", sa.Integer(), nullable=False),
        sa.Column("memory_mib", sa.Integer(), nullable=False),
        sa.Column("cpu_arch", sa.Text(), nullable=False),
        sa.Column("cpu_vendor", sa.Text()),
        sa.Column("gpu_model", sa.Text()),
        sa.Column("gpu_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("local_nvme_gib", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("network_bandwidth_gbps", sa.Float()),
        sa.Column("os_support", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("available_in_region", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("restricted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("raw", postgresql.JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "provider", "region", "sku", "catalog_version",
            name="uq_catalog_instance_types",
        ),
    )
    op.create_index(
        "ix_catalog_instance_types_provider_region",
        "catalog_instance_types",
        ["provider", "region"],
    )
    op.create_index("ix_catalog_instance_types_sku", "catalog_instance_types", ["sku"])

    # ------------------------------------------------------------------
    # catalog_prices
    # ------------------------------------------------------------------
    op.create_table(
        "catalog_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("catalog_version", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=False), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("os_type", sa.Text(), nullable=False),
        sa.Column("license_model", sa.Text(), nullable=False),
        sa.Column("term", sa.Text(), nullable=False),
        sa.Column("price_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="USD"),
        sa.Column("price_per", sa.Text(), nullable=False, server_default="hour"),
        sa.Column("raw", postgresql.JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "provider", "region", "sku", "os_type", "license_model", "term", "catalog_version",
            name="uq_catalog_prices",
        ),
    )
    op.create_index(
        "ix_catalog_prices_provider_region_sku",
        "catalog_prices",
        ["provider", "region", "sku"],
    )

    # ------------------------------------------------------------------
    # fx_rates
    # ------------------------------------------------------------------
    op.create_table(
        "fx_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("base_currency", sa.Text(), nullable=False, server_default="USD"),
        sa.Column("target_currency", sa.Text(), nullable=False),
        sa.Column("rate", sa.Numeric(12, 6), nullable=False),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="ecb"),
        sa.UniqueConstraint(
            "base_currency", "target_currency", "rate_date",
            name="uq_fx_rates",
        ),
    )
    op.create_index(
        "ix_fx_rates_target_currency_date",
        "fx_rates",
        ["target_currency", "rate_date"],
    )

    # ------------------------------------------------------------------
    # catalog_disk_prices
    # ------------------------------------------------------------------
    op.create_table(
        "catalog_disk_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("catalog_version", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=False), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("disk_type", sa.Text(), nullable=False),
        sa.Column("price_per_gib_month", sa.Numeric(12, 6)),
        sa.Column("price_per_iops_month", sa.Numeric(12, 6)),
        sa.Column("price_per_mbps_month", sa.Numeric(12, 6)),
        sa.Column("max_size_gib", sa.Integer()),
        sa.Column("max_iops", sa.Integer()),
        sa.Column("max_throughput_mbps", sa.Integer()),
        sa.Column("raw", postgresql.JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_catalog_disk_prices_provider_region",
        "catalog_disk_prices",
        ["provider", "region"],
    )
    op.create_index(
        "ix_catalog_disk_prices_disk_type",
        "catalog_disk_prices",
        ["disk_type"],
    )


def downgrade() -> None:
    tables = [
        "catalog_disk_prices",
        "fx_rates",
        "catalog_prices",
        "catalog_instance_types",
    ]
    for table in tables:
        op.drop_table(table)
