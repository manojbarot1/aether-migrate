"""Enforce the container hardening baseline (PROJECT_PLAN §19.2) on the resolved stack.

Usage: docker compose -f deploy/compose/compose.yaml config --format json | python3 deploy/scripts/lint_compose.py
"""

import json
import sys

# Services that legitimately publish ports in the base (production) stack.
PUBLISHERS = {"edge"}
# One-shot jobs and third-party images that manage their own writable state.
WRITABLE_ROOTFS_OK = {"edge", "postgres", "keycloak", "temporal", "temporal-schema", "temporal-namespace"}
# Networks with a route out of the host, and the only services allowed on each.
EGRESS = {"cloud-egress": {"worker-connector"}, "llm-egress": {"assistant"}}
# Services that must never hold secret-store access (they talk to the outside world).
NO_SECRETS_NETWORK = {"assistant"}


def main() -> int:
    cfg = json.load(sys.stdin)
    errors: list[str] = []
    for name, svc in cfg["services"].items():
        image = svc.get("image", "")

        def err(msg: str, name: str = name) -> None:
            errors.append(f"{name}: {msg}")

        if image.endswith(":latest") or (":" not in image.rsplit("/", 1)[-1] and "@" not in image):
            err(f"image must be pinned to a version or digest (got {image!r})")
        if svc.get("privileged"):
            err("privileged containers are forbidden")
        if svc.get("network_mode") == "host" or svc.get("pid") == "host":
            err("host network/pid namespaces are forbidden")
        if "ALL" not in (svc.get("cap_drop") or []):
            err("must drop ALL capabilities")
        if svc.get("cap_add"):
            err(f"cap_add is forbidden ({svc['cap_add']})")
        if "no-new-privileges:true" not in (svc.get("security_opt") or []):
            err("must set no-new-privileges")
        if not svc.get("read_only") and name not in WRITABLE_ROOTFS_OK:
            err("root filesystem must be read-only")
        for vol in svc.get("volumes") or []:
            if "docker.sock" in str(vol.get("source", "")):
                err("mounting the Docker socket is forbidden")
        if svc.get("ports") and name not in PUBLISHERS:
            err("only the edge proxy may publish ports")
        long_running = svc.get("restart") not in (None, "no")
        if long_running and not svc.get("mem_limit") and not svc.get("deploy", {}).get("resources"):
            err("long-running services need a memory limit")
        nets = set(svc.get("networks") or {})
        for net, allowed in EGRESS.items():
            if net in nets and name not in allowed:
                err(f"only {sorted(allowed)} may join the {net} network")
        if name in NO_SECRETS_NETWORK and "secrets" in nets:
            err("must not join the secrets network")
        env = svc.get("environment") or {}
        for key, value in env.items():
            if any(s in key.upper() for s in ("PASSWORD", "SECRET", "TOKEN")) and value and not key.endswith("_FILE"):
                err(f"secret-looking environment variable {key}; use Docker secrets")
    for net, props in (cfg.get("networks") or {}).items():
        base = net.removeprefix(f"{cfg.get('name', '')}_")
        if base not in EGRESS and base != "edge" and not props.get("internal"):
            errors.append(f"network {base}: must be internal (only {sorted(EGRESS)} and edge may route out)")
    for e in errors:
        print(f"::error::{e}")
    print(f"{len(cfg['services'])} services checked, {len(errors)} violation(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
