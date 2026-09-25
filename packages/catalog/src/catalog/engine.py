"""AETHER MIGRATE — catalog engine stub."""


class CatalogEngine:
    """Full implementation in Phase 2.

    Stores and queries instance-type and pricing records from provider
    catalog syncs.
    """

    async def sync(self, provider, regions):
        """Sync catalog data from *provider* for the given *regions*."""
        raise NotImplementedError

    def query(self, filters):
        """Return catalog records matching *filters*."""
        raise NotImplementedError
