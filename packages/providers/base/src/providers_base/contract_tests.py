"""Abstract contract tests for ProviderAdapter implementations.

Every provider adapter MUST pass these tests. To run them for a concrete
adapter, create a test module in the provider's test suite that subclasses
``ProviderAdapterContractTests`` and implements the fixtures.

Example (in ``packages/providers/aws/tests/test_contract.py``)::

    from providers_base.contract_tests import ProviderAdapterContractTests
    from aws.adapter import AWSAdapter

    class TestAWSAdapterContract(ProviderAdapterContractTests):
        @pytest.fixture
        def adapter(self):
            return AWSAdapter()

        @pytest.fixture
        def sample_raw_resource(self):
            return RawResource(
                provider=ProviderName.aws,
                kind=ResourceKind.vm,
                native_id="i-0123456789abcdef0",
                region="us-east-1",
                account="123456789012",
                data={"InstanceId": "i-0123456789abcdef0", "InstanceType": "m5.xlarge"},
            )
"""

from __future__ import annotations

import pytest
from core.models import NormalizedBundle, ProviderName

from providers_base.adapter import (
    AdapterCapabilities,
    ConnCtx,
    ConnectionTestResult,
    ProviderAdapter,
    RawResource,
)


class ProviderAdapterContractTests:
    """Reusable pytest contract test suite for ``ProviderAdapter``.

    Subclass this and provide the required fixtures to validate any adapter.
    """

    # -------------------------------------------------------------------
    # Fixtures — must be overridden by subclasses
    # -------------------------------------------------------------------

    @pytest.fixture
    def adapter(self) -> ProviderAdapter:
        """Return an instance of the adapter under test."""
        raise NotImplementedError(
            "Override the `adapter` fixture in your ProviderAdapterContractTests subclass."
        )

    @pytest.fixture
    def sample_raw_resource(self) -> RawResource:
        """Return a sample RawResource for normalization tests."""
        raise NotImplementedError(
            "Override the `sample_raw_resource` fixture in your ProviderAdapterContractTests subclass."
        )

    # -------------------------------------------------------------------
    # Contract tests
    # -------------------------------------------------------------------

    def test_adapter_has_provider_attribute(self, adapter: ProviderAdapter) -> None:
        """Adapter must declare its ProviderName."""
        assert isinstance(adapter.provider, ProviderName)

    def test_adapter_has_capabilities(self, adapter: ProviderAdapter) -> None:
        """Adapter must expose an AdapterCapabilities instance."""
        assert isinstance(adapter.capabilities, AdapterCapabilities)

    def test_normalize_is_pure(
        self, adapter: ProviderAdapter, sample_raw_resource: RawResource
    ) -> None:
        """normalize() must be synchronous and must not perform network I/O.

        We validate by calling it twice and checking that the output is
        structurally equivalent (idempotent).
        """
        result1 = adapter.normalize(sample_raw_resource)
        result2 = adapter.normalize(sample_raw_resource)

        assert isinstance(result1, NormalizedBundle)
        assert isinstance(result2, NormalizedBundle)
        # Both calls must return the same number of resources/edges
        assert len(result1.resources) == len(result2.resources)
        assert len(result1.edges) == len(result2.edges)

    @pytest.mark.asyncio
    async def test_connection_test_returns_result(self, adapter: ProviderAdapter) -> None:
        """test_connection() must return a ConnectionTestResult (even on failure)."""
        import uuid

        ctx = ConnCtx(
            connection_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            account="test-account",
        )
        try:
            result = await adapter.test_connection(ctx)
            assert isinstance(result, ConnectionTestResult)
            assert isinstance(result.ok, bool)
        except NotImplementedError:
            pytest.skip("test_connection not yet implemented")

    @pytest.mark.asyncio
    async def test_list_regions_returns_list(self, adapter: ProviderAdapter) -> None:
        """list_regions() must return a list (possibly empty on auth failure)."""
        import uuid

        ctx = ConnCtx(
            connection_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            account="test-account",
        )
        try:
            regions = await adapter.list_regions(ctx)
            assert isinstance(regions, list)
        except NotImplementedError:
            pytest.skip("list_regions not yet implemented")
