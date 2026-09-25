"""AETHER MIGRATE — secrets service package.

Provides ``OpenBaoClient`` for KV-v2 credential storage and Transit encryption.
"""

from secrets_svc.client import OpenBaoClient

__all__ = ["OpenBaoClient"]
