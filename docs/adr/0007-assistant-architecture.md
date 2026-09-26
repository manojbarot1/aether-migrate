# ADR 0007 — Assistant: own tool loop, separate egress service, redaction by default

**Status:** accepted (supersedes the Pydantic AI suggestion in PROJECT_PLAN §9)

**Context.** The assistant must answer questions about customer estates without being able to change them. It has to run on external models (Claude) and on local ones (Ollama). It must keep customer identifiers inside the platform when a workspace requires it. Tool results contain text controlled by whoever can tag a VM.

**Decision.**

1. **Typed tool registry (`aether.tools`)** is the single source of tools for the in-product assistant and the MCP server. Each tool is a thin adapter over the REST endpoint function, so validation, audit records and row-level security are identical. Every call re-checks the caller's workspace role.
   - Side-effect classes are `read`, `read_workflow` (starts a discovery that only reads) and `draft`.
   - No tool mutates a cloud, approves, executes or touches credentials.
2. **Our own model gateway and loop** replace a framework such as Pydantic AI. The loop has to own:
   - redaction and restoration around every model call;
   - per-turn limits on steps, tool calls and side effects;
   - usage logging without prompt content;
   - replay of provider-native blocks (Claude thinking signatures and fallback markers).

   A framework would sit in the middle of all four. Claude is called through the official `anthropic` SDK, with streaming, adaptive thinking, prompt caching, and server-side refusal fallbacks. Ollama is called through its native chat API. Model ids are configuration.
3. **A separate `assistant` service** is the only process with a route to model providers (`llm-egress`).
   - It holds no OpenBao credentials and cannot reach OpenBao.
   - The main API keeps no internet route.
   - Local models run in an Ollama container on an internal network with no route out. Models are pulled by a short-lived helper.
4. **Data egress is a per-workspace policy**, fixed per conversation:
   - `external_redacted` (default): IPs/CIDRs, account ids, ARNs, e-mails, host names, resource names and tag values become stable placeholders (`{{ip_3}}`). They are restored in streamed text and in tool arguments.
   - `local_only`: only a local model may be used.
   - `external_allowed`: data is sent as-is. This is also required before external MCP clients may call tools, because they may forward results to any model.
5. **Untrusted data handling.** Tool results reach the model inside an `untrusted_data` envelope.
   - Strings are normalised, stripped of invisible and bidirectional characters, and truncated.
   - Values that read like instructions are removed. The user is told and the attempt is audited.
   - Model output is rendered as Markdown without raw HTML or images, and links are followed only inside the product.
   - Cards show exact figures from our API. The model's prose is told to only summarise them.

**Consequences.**
- There is more code to own than with a framework, but every security property is local and tested (unit tests for redaction, the guard and gateways; integration tests for the loop, RBAC, limits, budgets, retention and MCP on real Postgres).
- Local models on CPU are slow: about 9 prompt tokens/s on an 8-core laptop, so a first reply takes minutes. Production local use needs a GPU host or a smaller tool set.
- The `assistant` service has general internet egress. An allow-list proxy (only the model provider's host) is a follow-up, shared with the connector's egress.
