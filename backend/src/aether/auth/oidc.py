"""OIDC access-token verification against the identity provider's JWKS.

The JWKS is fetched over the internal network (``oidc_internal_base``) while the
``iss`` claim is checked against the *public* issuer URL, because that is what
the browser-facing IdP stamps into tokens.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

from aether.config import Settings

ALLOWED_ALGS = ["RS256", "ES256", "PS256"]


class TokenError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject: str
    email: str | None
    name: str | None
    realm_roles: frozenset[str]
    raw: dict[str, Any] = field(repr=False)


class JwksVerifier:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(timeout=5.0, transport=transport)
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _refresh(self, force: bool = False) -> None:
        async with self._lock:
            # Rate-limit refreshes to once per 30 s even when an unknown kid is presented.
            if not force and time.monotonic() - self._fetched_at < 300 and self._keys:
                return
            if force and time.monotonic() - self._fetched_at < 30:
                return
            resp = await self._http.get(self._settings.oidc_jwks_url)
            resp.raise_for_status()
            keys: dict[str, jwt.PyJWK] = {}
            for jwk in resp.json().get("keys", []):
                if jwk.get("use", "sig") != "sig" or jwk.get("alg") not in ALLOWED_ALGS:
                    continue
                keys[jwk["kid"]] = jwt.PyJWK(jwk)
            self._keys = keys
            self._fetched_at = time.monotonic()

    async def _key_for(self, kid: str) -> jwt.PyJWK:
        await self._refresh()
        if kid not in self._keys:
            await self._refresh(force=True)
        try:
            return self._keys[kid]
        except KeyError:
            raise TokenError("unknown signing key") from None

    async def verify(self, token: str) -> TokenClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise TokenError("malformed token") from e
        kid = header.get("kid")
        if not kid or header.get("alg") not in ALLOWED_ALGS:
            raise TokenError("unsupported token header")
        key = await self._key_for(kid)
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=ALLOWED_ALGS,
                audience=self._settings.oidc_audience,
                issuer=self._settings.oidc_issuer,
                options={"require": ["exp", "iat", "iss", "sub", "aud"]},
                leeway=30,
            )
        except jwt.PyJWTError as e:
            raise TokenError(f"invalid token: {type(e).__name__}") from e
        roles = claims.get("realm_access", {}).get("roles", [])
        return TokenClaims(
            subject=claims["sub"],
            email=claims.get("email"),
            name=claims.get("name") or claims.get("preferred_username"),
            realm_roles=frozenset(roles),
            raw=claims,
        )
