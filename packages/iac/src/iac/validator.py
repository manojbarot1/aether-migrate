"""OpenTofu module validator for AETHER MIGRATE.

Writes the TofuModule files to a temp directory, runs:
  tofu init -backend=false
  tofu validate
and returns a TofuValidationResult.

If the tofu binary is not found on PATH the function returns a
TofuValidationResult with valid=False and a clear message — it does not raise.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from iac.models import TofuModule, TofuValidationResult


async def validate_tofu_module(
    module: TofuModule,
    tofu_binary: str = "tofu",
) -> TofuValidationResult:
    """Validate a TofuModule by running ``tofu init`` + ``tofu validate``.

    Parameters
    ----------
    module:
        The TofuModule whose files will be written to a temp directory.
    tofu_binary:
        Path or name of the tofu binary.  Defaults to ``"tofu"``.

    Returns
    -------
    TofuValidationResult
        ``valid=True`` when both commands succeed with no errors.
    """
    resolved = shutil.which(tofu_binary)
    if resolved is None:
        return TofuValidationResult(
            valid=False,
            errors=["tofu binary not found — install OpenTofu to validate"],
            warnings=[],
        )

    with tempfile.TemporaryDirectory(prefix="aether_tofu_") as tmpdir:
        tmp_path = Path(tmpdir)

        # Write all module files
        for filename, content in module.files.items():
            (tmp_path / filename).write_text(content, encoding="utf-8")

        errors: list[str] = []
        warnings: list[str] = []

        # --- tofu init -backend=false ---
        init_result = subprocess.run(
            [resolved, "init", "-backend=false", "-no-color"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if init_result.returncode != 0:
            stderr = init_result.stderr.strip()
            stdout = init_result.stdout.strip()
            errors.append(f"tofu init failed: {stderr or stdout}")
            return TofuValidationResult(valid=False, errors=errors, warnings=warnings)

        # --- tofu validate ---
        validate_result = subprocess.run(
            [resolved, "validate", "-no-color"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if validate_result.returncode != 0:
            stderr = validate_result.stderr.strip()
            stdout = validate_result.stdout.strip()
            for line in (stderr or stdout).splitlines():
                line = line.strip()
                if line.startswith("Warning:"):
                    warnings.append(line)
                elif line:
                    errors.append(line)
            return TofuValidationResult(
                valid=False,
                errors=errors,
                warnings=warnings,
            )

        # Parse any warnings from stdout
        for line in validate_result.stdout.splitlines():
            line = line.strip()
            if line.startswith("Warning:"):
                warnings.append(line)

        return TofuValidationResult(valid=True, errors=[], warnings=warnings)
