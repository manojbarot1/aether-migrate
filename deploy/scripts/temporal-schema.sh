#!/bin/sh
# Idempotent Temporal schema setup/upgrade for PostgreSQL (runs in temporalio/admin-tools).
set -eu
export SQL_PLUGIN=postgres12 SQL_HOST=postgres SQL_PORT=5432 SQL_USER=temporal
SQL_PASSWORD="$(cat /run/secrets/pg_temporal_password)"
export SQL_PASSWORD
SCHEMA=/etc/temporal/schema/postgresql/v12

for db in temporal temporal_visibility; do
  dir=$SCHEMA/temporal
  [ "$db" = temporal_visibility ] && dir=$SCHEMA/visibility
  # setup-schema -v 0.0 creates the version table on a fresh database; it fails
  # harmlessly (and is skipped) when the schema already exists.
  temporal-sql-tool --db "$db" setup-schema -v 0.0 2>/dev/null || true
  temporal-sql-tool --db "$db" update-schema -d "$dir/versioned"
done
echo "temporal schema up to date"
