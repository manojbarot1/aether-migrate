"""Tests for plan export functions (JSON, Markdown, ZIP)."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime

from core.models import ProviderName
from planner.models import MigrationPlan, PlanStep, ResourceSummary


def _make_migration_plan() -> MigrationPlan:
    now = datetime.now(UTC)
    step = PlanStep(
        step_number=1,
        title="Pre-flight check",
        description="Verify all prerequisites.",
        pre_check="Check permissions.",
        action="Validate access.",
        post_check="All checks pass.",
        compensation="Do not proceed.",
        estimated_duration_minutes=30,
        is_manual=True,
    )
    return MigrationPlan(
        plan_id="test-plan-001",
        workspace_id="ws-001",
        name="Test Migration Plan",
        version=1,
        source_resources=[
            ResourceSummary(
                id="res-001",
                name="web-01",
                kind="vm",
                provider="aws",
                region="us-east-1",
            )
        ],
        target_provider=ProviderName.azure,
        target_region="eastus",
        sizing_choices=[],
        assessment_findings=[],
        acknowledged_findings=[],
        prerequisites=["Create landing zone"],
        steps=[step],
        downtime_estimate_minutes=45,
        downtime_basis="500 GiB at 500 Mbps",
        rollback_plan="Revert DNS.",
        assumptions=["Network is available."],
        snapshot_id="snap-001",
        snapshot_time=now,
        catalog_version="2025-01",
        catalog_date=now,
        created_at=now,
        content_hash="a" * 64,
    )


class TestJsonExport:
    def test_json_export_is_valid_json(self):
        from planner.export import export_json

        plan = _make_migration_plan()
        output = export_json(plan)
        data = json.loads(output)
        assert isinstance(data, dict)

    def test_json_export_has_schema_version(self):
        from planner.export import export_json

        plan = _make_migration_plan()
        data = json.loads(export_json(plan))
        assert data["schema_version"] == "1.0"

    def test_json_export_contains_plan_name(self):
        from planner.export import export_json

        plan = _make_migration_plan()
        data = json.loads(export_json(plan))
        assert data["name"] == "Test Migration Plan"

    def test_json_export_has_steps(self):
        from planner.export import export_json

        plan = _make_migration_plan()
        data = json.loads(export_json(plan))
        assert len(data["steps"]) == 1
        assert data["steps"][0]["title"] == "Pre-flight check"


class TestMarkdownExport:
    def test_markdown_export_is_string(self):
        from planner.export import export_markdown

        plan = _make_migration_plan()
        output = export_markdown(plan)
        assert isinstance(output, str)
        assert len(output) > 100

    def test_markdown_export_contains_plan_name(self):
        from planner.export import export_markdown

        plan = _make_migration_plan()
        output = export_markdown(plan)
        assert "Test Migration Plan" in output

    def test_markdown_export_contains_steps(self):
        from planner.export import export_markdown

        plan = _make_migration_plan()
        output = export_markdown(plan)
        assert "Pre-flight check" in output
        assert "Step 1" in output

    def test_markdown_export_contains_prerequisites(self):
        from planner.export import export_markdown

        plan = _make_migration_plan()
        output = export_markdown(plan)
        assert "Create landing zone" in output

    def test_markdown_export_contains_downtime(self):
        from planner.export import export_markdown

        plan = _make_migration_plan()
        output = export_markdown(plan)
        assert "45 minutes" in output or "45" in output


class TestZipExport:
    def test_zip_export_is_bytes(self):
        from iac.generators.azure import AzureTofuGenerator
        from planner.export import export_tofu_zip

        plan = _make_migration_plan()

        # Use a real generator
        gen = AzureTofuGenerator()
        tofu_module = gen.generate(plan)
        content = export_tofu_zip(plan, tofu_module)

        assert isinstance(content, bytes)
        assert len(content) > 0

    def test_zip_export_contains_main_tf(self):
        from iac.generators.azure import AzureTofuGenerator
        from planner.export import export_tofu_zip

        plan = _make_migration_plan()
        gen = AzureTofuGenerator()
        tofu_module = gen.generate(plan)
        content = export_tofu_zip(plan, tofu_module)

        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = zf.namelist()
        assert any("main.tf" in n for n in names), f"main.tf not found. Files: {names}"

    def test_zip_export_contains_readme(self):
        from iac.generators.azure import AzureTofuGenerator
        from planner.export import export_tofu_zip

        plan = _make_migration_plan()
        gen = AzureTofuGenerator()
        tofu_module = gen.generate(plan)
        content = export_tofu_zip(plan, tofu_module)

        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = zf.namelist()
        assert any("README.md" in n for n in names), f"README.md not found. Files: {names}"

    def test_zip_export_contains_all_module_files(self):
        from iac.generators.azure import AzureTofuGenerator
        from planner.export import export_tofu_zip

        plan = _make_migration_plan()
        gen = AzureTofuGenerator()
        tofu_module = gen.generate(plan)
        content = export_tofu_zip(plan, tofu_module)

        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = [n.split("/")[-1] for n in zf.namelist()]

        for expected in ["main.tf", "network.tf", "compute.tf", "variables.tf", "outputs.tf", "versions.tf"]:
            assert expected in names, f"Expected {expected} in ZIP"
