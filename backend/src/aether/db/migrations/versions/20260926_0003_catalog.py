"""catalog: syncs, instance specs, prices, disk prices, fx rates (global reference data)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "aether_app"
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "catalog_syncs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", TS),
        sa.Column("regions", pg.JSONB),
        sa.Column("stats", pg.JSONB),
        sa.Column("error", sa.Text),
    )
    op.create_table(
        "catalog_instance_specs",
        sa.Column("provider", sa.String(16), primary_key=True),
        sa.Column("sku", sa.String(64), primary_key=True),
        sa.Column("family", sa.String(32), nullable=False),
        sa.Column("vcpu", sa.Integer, nullable=False),
        sa.Column("memory_mib", sa.Integer, nullable=False),
        sa.Column("cpu_arch", sa.String(16), nullable=False),
        sa.Column("cpu_vendor", sa.String(32)),
        sa.Column("gpu_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("gpu_model", sa.String(64)),
        sa.Column("local_disk_gib", sa.Integer, nullable=False, server_default="0"),
        sa.Column("generation", sa.Integer, nullable=False, server_default="0"),
        sa.Column("spec_source", sa.String(32), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "catalog_prices",
        sa.Column("provider", sa.String(16), primary_key=True),
        sa.Column("region", sa.String(64), primary_key=True),
        sa.Column("sku", sa.String(64), primary_key=True),
        sa.Column("os", sa.String(16), primary_key=True),
        sa.Column("model", sa.String(16), primary_key=True),
        sa.Column("hourly_usd", sa.Float, nullable=False),
        sa.Column("effective_from", TS),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("sync_id", pg.UUID(as_uuid=True)),
        sa.Column("fetched_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "catalog_disk_prices",
        sa.Column("provider", sa.String(16), primary_key=True),
        sa.Column("region", sa.String(64), primary_key=True),
        sa.Column("disk_class", sa.String(32), primary_key=True),
        sa.Column("tier", sa.String(16), primary_key=True),
        sa.Column("size_gib", sa.Integer),
        sa.Column("iops", sa.Integer),
        sa.Column("monthly_usd", sa.Float),
        sa.Column("gib_month_usd", sa.Float),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("fetched_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "fx_rates",
        sa.Column("currency", sa.String(3), primary_key=True),
        sa.Column("per_usd", sa.Float, nullable=False),
        sa.Column("rate_date", TS, nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("fetched_at", TS, server_default=sa.func.now(), nullable=False),
    )
    tables = "catalog_syncs, catalog_instance_specs, catalog_prices, catalog_disk_prices, fx_rates"
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {tables} TO {APP_ROLE}")


def downgrade() -> None:
    for t in ("fx_rates", "catalog_disk_prices", "catalog_prices", "catalog_instance_specs", "catalog_syncs"):
        op.drop_table(t)
