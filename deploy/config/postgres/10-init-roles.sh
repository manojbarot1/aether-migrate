#!/usr/bin/env bash
# Runs once, on an empty data directory, as the postgres superuser.
# Creates one owner + one runtime role per application database. Passwords come
# from Docker secrets; nothing sensitive is passed on the command line.
set -euo pipefail

read_secret() { tr -d '\n' < "/run/secrets/$1"; }

psql_su() { psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${1:-postgres}" "${@:2}"; }

psql_su postgres \
  -v owner_pw="$(read_secret pg_owner_password)" \
  -v app_pw="$(read_secret pg_app_password)" <<'SQL'
CREATE ROLE aether_owner LOGIN PASSWORD :'owner_pw';
CREATE ROLE aether_app LOGIN PASSWORD :'app_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE DATABASE aether OWNER aether_owner;
REVOKE ALL ON DATABASE aether FROM PUBLIC;
GRANT CONNECT ON DATABASE aether TO aether_app;
SQL

psql_su aether <<'SQL'
ALTER SCHEMA public OWNER TO aether_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO aether_app;
SQL

if [[ -f /run/secrets/pg_temporal_password ]]; then
  psql_su postgres -v pw="$(read_secret pg_temporal_password)" <<'SQL'
CREATE ROLE temporal LOGIN PASSWORD :'pw';
CREATE DATABASE temporal OWNER temporal;
CREATE DATABASE temporal_visibility OWNER temporal;
REVOKE ALL ON DATABASE temporal FROM PUBLIC;
REVOKE ALL ON DATABASE temporal_visibility FROM PUBLIC;
SQL
fi

if [[ -f /run/secrets/pg_keycloak_password ]]; then
  psql_su postgres -v pw="$(read_secret pg_keycloak_password)" <<'SQL'
CREATE ROLE keycloak LOGIN PASSWORD :'pw';
CREATE DATABASE keycloak OWNER keycloak;
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
SQL
fi
