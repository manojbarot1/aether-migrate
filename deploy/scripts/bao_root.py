"""Break-glass root token for OpenBao from a quorum of unseal keys.

Reads unseal keys (one per line) on stdin, prints a root token on stdout. Uses the
quorum-gated sys/generate-root endpoints. Run inside a container on the `secrets`
network (see lib.sh:with_root_token); keys never appear on a command line.
"""

import base64
import sys

import httpx

BASE = "http://openbao:8200/v1/sys/generate-root/attempt"


def main() -> int:
    keys = [line.strip() for line in sys.stdin if line.strip()]
    with httpx.Client(timeout=10) as c:
        c.delete(BASE)  # cancel any stale attempt
        attempt = c.put(BASE, json={}).raise_for_status().json()
        nonce, otp = attempt["nonce"], attempt["otp"]
        encoded = ""
        for key in keys:
            r = c.put(BASE.replace("/attempt", "/update"), json={"key": key, "nonce": nonce})
            if r.status_code != 200:
                print(f"unseal key rejected: {r.status_code}", file=sys.stderr)
                c.delete(BASE)
                return 1
            body = r.json()
            if body.get("complete"):
                encoded = body["encoded_token"]
                break
        if not encoded:
            print("quorum not reached", file=sys.stderr)
            c.delete(BASE)
            return 1
    raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4))
    print(bytes(a ^ b for a, b in zip(raw, otp.encode(), strict=False)).decode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
