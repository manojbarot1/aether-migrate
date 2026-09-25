# ADR-006: pydantic-ai for Model Orchestration

**Status:** Accepted  
**Date:** 2025-01-01

## Context

Phase 3 adds an AI assistant that can invoke AETHER MIGRATE tools (inventory queries, sizing recommendations, assessment generation). The orchestration layer must support multiple LLM providers (OpenAI, IBM watsonx, local Ollama), type-safe tool definitions, and structured output validation.

Options considered: LangChain, LlamaIndex, pydantic-ai.

## Decision

Use **pydantic-ai** for the AI orchestration layer. It builds directly on Pydantic v2 models, which are already used throughout the codebase for domain types and tool schemas. This means `ToolDefinition.input_schema` / `output_schema` (from `packages/tools`) can be passed directly to pydantic-ai agents without adapters.

pydantic-ai's multi-provider support covers OpenAI, Anthropic, Gemini, Ollama, and IBM watsonx via a unified interface.

## Consequences

- **+** Zero impedance mismatch with existing Pydantic v2 domain models.
- **+** Structured output validation is automatic via Pydantic.
- **+** Supports streaming, which is required for the SSE chat endpoint.
- **−** Younger project (v0.x) with a less stable API than LangChain.
- **−** Limited ecosystem of pre-built agents; more custom code required.
