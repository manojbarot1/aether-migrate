# ADR 0001 — One Python package with enforced import boundaries

**Status:** accepted (2026-09-26)

**Context.** v0.1.0 split the backend into 20 separately packaged workspace members, installed editable into every image. That added packaging friction without adding isolation: the API image still shipped `boto3` and the provider adapters.

**Decision.** Use a single package, `aether`, under `backend/`. Isolation happens in two places:

- **Images:** the `api` target has no cloud SDKs; the `connector` target adds them via the `connector` extra.
- **Imports:** `import-linter` contracts in CI. `aether.api` and `aether.tools` must not import `aether.providers.aws`, `boto3` or `botocore`. `aether.core` must not depend on infrastructure.

**Consequences.** One lockfile, one test suite, and a simple Dockerfile. The boundary is enforced by the build, not by convention.
