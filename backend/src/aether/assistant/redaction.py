"""Reversible redaction for the ``external_redacted`` egress mode.

Before anything leaves the platform, identifying values (IP addresses and CIDRs,
account ids, ARNs, e-mail addresses, fully-qualified host names, resource names and
tag values) are replaced with stable placeholders such as ``{{ip_3}}``. The mapping
("vault") is kept per conversation in our own database, so:

* the model can still reason about and refer to individual resources;
* tool arguments the model sends back are restored before execution;
* streamed model text is restored before the user sees it.

The same original value always maps to the same placeholder within a conversation.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

TOKEN_RE = re.compile(r"\{\{([a-z]+_\d+)\}\}")

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("arn", re.compile(r"\barn:aws[a-z-]*:[^\s\"',;]+")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    (
        "ip",
        re.compile(
            r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:/\d{1,2})?(?![\d.])"
        ),
    ),
    # IPv6: needs "::" or at least four groups, so clock times such as 15:05:52 stay readable.
    (
        "ip",
        re.compile(
            r"(?<![\w:])(?=[0-9a-fA-F:]*::|(?:[0-9a-fA-F]{1,4}:){4})"
            r"(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}(?:/\d{1,3})?(?![\w:])"
        ),
    ),
    ("acct", re.compile(r"(?<!\d)\d{12}(?!\d)")),
    # Host names with at least two dots and a letter (keeps "m5.large" and "22.04" readable).
    ("host", re.compile(r"\b(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+){2,}\b")),
]

# Keys whose string values identify something and are always tokenized.
_NAME_KEYS = frozenset(
    {"name", "hostname", "computer_name", "private_dns_name", "public_dns_name", "account"}
)
_TAG_KEYS = frozenset({"tags"})
_MIN_KNOWN = 3  # shorter known values are not substituted inside free text
_MARKER = "[removed"  # guardrail markers stay visible to the model


def _keep(value: object) -> bool:
    return value in (None, "") or (isinstance(value, str) and value.startswith(_MARKER))


class Vault:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.forward: dict[str, str] = dict(data.get("forward", {}))  # original -> token
        self.counters: dict[str, int] = dict(data.get("counters", {}))
        self.reverse: dict[str, str] = {v: k for k, v in self.forward.items()}
        self._known_re: re.Pattern[str] | None = None
        self.dirty = False

    def to_json(self) -> dict[str, Any]:
        return {"forward": self.forward, "counters": self.counters}

    def token(self, original: str, kind: str) -> str:
        existing = self.forward.get(original)
        if existing:
            return existing
        n = self.counters.get(kind, 0) + 1
        self.counters[kind] = n
        tok = f"{{{{{kind}_{n}}}}}"
        self.forward[original] = tok
        self.reverse[tok] = original
        self._known_re = None
        self.dirty = True
        return tok

    # ------------------------------------------------------------------ redact

    def _known(self) -> re.Pattern[str] | None:
        if self._known_re is None:
            keys = sorted((k for k in self.forward if len(k) >= _MIN_KNOWN), key=len, reverse=True)
            self._known_re = re.compile("|".join(re.escape(k) for k in keys)) if keys else None
        return self._known_re

    def redact_text(self, text: str) -> str:
        known = self._known()
        if known is not None:
            text = known.sub(lambda m: self.forward[m.group(0)], text)
        for kind, pattern in _PATTERNS:
            text = pattern.sub(lambda m, k=kind: self.token(m.group(0), k), text)  # type: ignore[misc]
        return text

    def redact(self, value: Any, key: str | None = None) -> Any:
        if isinstance(value, str):
            if key in _NAME_KEYS and not _keep(value):
                return self.token(value, "name")
            return self.redact_text(value)
        if isinstance(value, dict):
            if key in _TAG_KEYS:
                return {
                    self.redact_text(str(k)): (
                        v if _keep(v) else self.token(str(v), "name" if str(k).lower() == "name" else "tag")
                    )
                    for k, v in value.items()
                }
            return {k: self.redact(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact(v, key) for v in value]
        return value

    # ------------------------------------------------------------------ restore

    def restore_text(self, text: str) -> str:
        return TOKEN_RE.sub(lambda m: self.reverse.get(m.group(0), m.group(0)), text)

    def restore(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.restore_text(value)
        if isinstance(value, dict):
            return {
                self.restore_text(k) if isinstance(k, str) else k: self.restore(v) for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.restore(v) for v in value]
        return value


class NullVault(Vault):
    """Pass-through used for ``external_allowed`` and ``local_only``."""

    def redact(self, value: Any, key: str | None = None) -> Any:
        return value

    def redact_text(self, text: str) -> str:
        return text


class StreamRestorer:
    """Restores placeholders in streamed text whose tokens may be split across chunks."""

    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self.buf = ""

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        cut = self.buf.rfind("{")
        if cut != -1 and "}}" not in self.buf[cut:] and len(self.buf) - cut < 40:
            # Hold back a possibly incomplete "{{kind_n}}" tail (and a lone "{" before "{{").
            if cut > 0 and self.buf[cut - 1] == "{":
                cut -= 1
            out, self.buf = self.buf[:cut], self.buf[cut:]
        else:
            out, self.buf = self.buf, ""
        return self.vault.restore_text(out)

    def flush(self) -> str:
        out, self.buf = self.buf, ""
        return self.vault.restore_text(out)


def tokens_in(values: Iterable[str]) -> set[str]:
    found: set[str] = set()
    for v in values:
        found.update(m.group(0) for m in TOKEN_RE.finditer(v))
    return found
