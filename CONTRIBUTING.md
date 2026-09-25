# Contributing to AETHER MIGRATE

Thank you for contributing! This document describes the development workflow.

---

## Development Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Node.js 20+ (for the web SPA)
- Docker Engine 25+ and Compose v2

### First-time setup

```bash
# Clone and enter the repo
git clone https://github.com/your-org/aether-migrate
cd aether-migrate

# Install Python workspace (all packages)
uv sync

# Install web dependencies
cd apps/web && npm ci && cd ../..

# Bootstrap infrastructure
make bootstrap
```

---

## Development Workflow

### Running tests

```bash
make test          # full test suite with coverage
make test-fast     # faster, no coverage
```

### Linting and formatting

```bash
make lint          # ruff + ESLint
make lint-fix      # auto-fix issues
make format        # format Python files
make typecheck     # mypy strict mode
```

### Running in dev mode

```bash
make dev           # starts all services with hot reload
```

The API reloads on Python file changes. The web SPA uses Vite's HMR.

---

## Code Standards

### Python

- All code uses Python 3.12+ syntax.
- Pydantic v2 for all data models (`model_validate`, not `parse_obj`).
- `structlog` for all logging (JSON format). **Never use `print()` in production code.**
- `from __future__ import annotations` at the top of every file.
- Type annotations required on all public functions and methods.
- `async/await` throughout; no synchronous DB calls.

### TypeScript / React

- React 19 with functional components and hooks only.
- Strict TypeScript: no `any` without a comment.
- TanStack Query for all server state; no `useEffect` for data fetching.

### Security

- **No hardcoded secrets**. Credentials come from environment variables or OpenBao.
- **No `0.0.0.0` binds**. Services bind to `127.0.0.1` inside containers.
- **No stack traces to clients**. Log them server-side; return generic error messages.
- Input validation via Pydantic models; no `eval()` or `exec()`.

---

## Adding a New Provider

1. Create `packages/providers/{name}/` with `pyproject.toml`, `__init__.py`, and `adapter.py`.
2. Implement `{Name}Adapter(ProviderAdapter)` with all protocol methods.
3. Add to the workspace in the root `pyproject.toml`.
4. Subclass `ProviderAdapterContractTests` in `packages/providers/{name}/tests/test_contract.py`.
5. Add the new provider to `worker_connector/pyproject.toml` dependencies.

---

## Pull Request Process

1. Fork the repository and create a feature branch.
2. Ensure all tests pass: `make test`.
3. Ensure linting passes: `make lint && make typecheck`.
4. Write a clear PR description referencing the relevant phase/ADR.
5. Request review from a maintainer.

---

## Commit Message Convention

```
type(scope): short description

feat(api): add snapshot filtering by status
fix(audit): correct hash chain prev_hash on first event
docs(adr): add ADR-009 for observability stack
chore(deps): upgrade pydantic to 2.10.0
```

Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`
