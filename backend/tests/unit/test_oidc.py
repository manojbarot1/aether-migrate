import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from aether.auth.oidc import JwksVerifier, TokenError
from aether.config import Settings

ISSUER = "https://aether.test/auth/realms/aether"


@pytest.fixture(scope="module")
def keypair() -> tuple[rsa.RSAPrivateKey, dict[str, str]]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="k1", alg="RS256", use="sig")
    return key, jwk


def _verifier(jwk: dict[str, str]) -> JwksVerifier:
    settings = Settings(
        oidc_issuer=ISSUER, oidc_internal_base="http://kc/realms/aether", oidc_audience="aether-api"
    )
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"keys": [jwk]}))
    return JwksVerifier(settings, transport=transport)


def _token(key: rsa.RSAPrivateKey, **overrides: object) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": ["aether-api", "account"],
        "sub": "user-1",
        "iat": now,
        "exp": now + 300,
        "email": "a@example.com",
        "realm_access": {"roles": ["platform-admin", "default-roles-aether"]},
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


async def test_valid_token(keypair) -> None:  # type: ignore[no-untyped-def]
    key, jwk = keypair
    claims = await _verifier(jwk).verify(_token(key))
    assert claims.subject == "user-1"
    assert "platform-admin" in claims.realm_roles


@pytest.mark.parametrize(
    "override",
    [{"iss": "https://evil.test/realms/aether"}, {"aud": "other"}, {"exp": int(time.time()) - 3600}],
)
async def test_rejects_bad_claims(keypair, override) -> None:  # type: ignore[no-untyped-def]
    key, jwk = keypair
    with pytest.raises(TokenError):
        await _verifier(jwk).verify(_token(key, **override))


async def test_rejects_foreign_key_and_alg_none(keypair) -> None:  # type: ignore[no-untyped-def]
    _, jwk = keypair
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    v = _verifier(jwk)
    with pytest.raises(TokenError):
        await v.verify(_token(other))
    unsigned = jwt.encode({"sub": "x", "iss": ISSUER}, key=None, algorithm="none")
    with pytest.raises(TokenError):
        await v.verify(unsigned)
