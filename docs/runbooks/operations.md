# Operations runbook

All commands run from the repository root on the Docker host.

## After a host reboot: unseal OpenBao

OpenBao starts **sealed**. Until it is unsealed, the API reports `openbao: fail` on `/readyz`, and connection tests and credential changes fail. Reads of existing metadata keep working.

```bash
deploy/scripts/aetherctl unseal   # prompts for 3 of 5 unseal keys when openbao-init.json is not on the host
deploy/scripts/aetherctl ps
```

Key custody: 5 shares, threshold 3. Give each share to a different person. Never store 3 or more shares together, and never keep them on the Docker host in production.

## Health and controls

```bash
deploy/scripts/aetherctl ps
deploy/scripts/aetherctl selftest    # must print "15 passed, 0 failed"
```

Run `selftest` after every upgrade, restore or configuration change. Keep its output as audit evidence.

## Backup

```bash
deploy/scripts/aetherctl backup /srv/backups/aether/$(date +%F)
```

This produces:

- a `pg_dump -Fc` of `aether`, `keycloak`, `temporal` and `temporal_visibility`;
- the roles DDL, without passwords;
- an **OpenBao Raft snapshot**, taken with the dedicated `aether-backup` AppRole, which can only read `sys/storage/raft/snapshot`;
- a `SHA256SUMS` file and a `MANIFEST`.

The snapshot is encrypted by OpenBao's barrier: restoring it requires the unseal keys. Copy backups offsite, and keep them separate from the unseal keys. Schedule it with cron or a systemd timer, e.g. nightly:

```
15 2 * * *  cd /opt/aether-migrate && deploy/scripts/aetherctl backup >/var/log/aether-backup.log 2>&1
```

## Restore (and quarterly restore drill)

```bash
deploy/scripts/aetherctl restore /srv/backups/aether/2026-09-26
deploy/scripts/aetherctl selftest
```

The restore:

1. verifies checksums;
2. asks for confirmation;
3. stops the application services;
4. restores the four databases;
5. restores OpenBao using a break-glass root token (quorum of unseal keys);
6. restarts the stack.

Set `AETHER_RESTORE_CONFIRM=yes` for unattended drills on a scratch host.

## Break-glass OpenBao administration

The initial root token is revoked during bootstrap. To change policies or re-issue AppRole credentials:

```bash
deploy/scripts/aetherctl bao-reconfigure
```

This mints a temporary root token from a quorum of unseal keys (OpenBao's `sys/generate-root`), re-applies `deploy/config/openbao/policy-*.hcl`, issues any missing AppRole credentials, and revokes the token. Every step appears in the OpenBao audit log (`openbao-logs` volume).

### Rotate an AppRole secret-id

```bash
rm deploy/secrets/bao_connector_secret_id deploy/secrets/bao_connector_role_id
deploy/scripts/aetherctl bao-reconfigure
docker compose --project-directory deploy/compose -f deploy/compose/compose.yaml --env-file .env up -d --force-recreate worker-connector
```

## Upgrade

```bash
git fetch && git checkout vX.Y.Z
deploy/scripts/aetherctl backup
deploy/scripts/aetherctl up        # builds, runs DB migrations (one-shot `migrate` job), restarts
deploy/scripts/aetherctl selftest
```

Database migrations are forward-only in production. To roll back, restore the pre-upgrade backup.

## Changing the public URL

1. Update `AETHER_PUBLIC_URL` / `AETHER_DOMAIN` (and `AETHER_TLS`) in `.env`.
2. Run `aetherctl up`.
3. Run `aetherctl sync-idp` so the OIDC client accepts the new redirect URI.

## Users and access

- **Platform administrators** hold the `platform-admin` realm role in Keycloak (realm `aether`). They create workspaces and act as admin in all of them.
- **Workspace roles** (viewer … admin) are granted in the app under **Members**. The user must have signed in once.
- Keycloak's admin console (`/auth/admin`) is reachable only from private networks. Its master-realm admin password is in `deploy/secrets/keycloak_admin_password`.

## Logs

```bash
deploy/scripts/aetherctl logs api worker-connector
```

All application logs are structured JSON with a `request_id`. Secrets are redacted by key name and by pattern (AWS keys, JWTs, private keys, OpenBao tokens) before logs are written.
