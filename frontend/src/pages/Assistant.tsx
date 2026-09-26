import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowUp, Bot, Check, ChevronRight, Loader2, Lock, MessageSquarePlus, Settings2, ShieldCheck, Square, Trash2, Wrench, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Markdown, { type Components } from "react-markdown";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import remarkGfm from "remark-gfm";
import { ResultCard } from "../components/AssistantCards";
import { Badge, Button, Dialog, ErrorBanner, Field, Input, Select, Spinner, relativeTime } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useApi, useWorkspace } from "../lib/context";
import type { AssistantSettings, ConversationMessage, EgressMode, StreamEvent, ToolResultView } from "../lib/types";

type Part =
  | { kind: "text"; text: string }
  | { kind: "tool"; id: string; name: string; title: string; args: Record<string, unknown>; result?: ToolResultView };

interface Turn {
  key: string;
  user: string;
  parts: Part[];
  notices: string[];
  error?: string | null;
  live?: boolean;
}

const EGRESS: Record<EgressMode, { label: string; detail: string; tone: "ok" | "warn" | "accent" }> = {
  external_redacted: {
    label: "Redacted",
    detail: "Names, IPs, account ids and tag values are replaced with placeholders before anything is sent to the model, and restored for you.",
    tone: "ok",
  },
  local_only: { label: "Local model", detail: "Nothing leaves this deployment; a local model answers.", tone: "ok" },
  external_allowed: { label: "External", detail: "Inventory data is sent to the external model provider as-is.", tone: "warn" },
};

const SUGGESTIONS = [
  "Summarise my inventory and how current it is",
  "Which VMs have at least 8 vCPUs and 32 GB of memory?",
  "What would my Linux VMs cost in Azure West Europe?",
  "Which machines are blocked from migrating, and why?",
];

function toTurns(messages: ConversationMessage[]): Turn[] {
  const results = new Map<string, ToolResultView>();
  for (const m of messages) if (m.role === "tool") for (const r of m.display.results ?? []) results.set(r.call_id, r);
  const turns: Turn[] = [];
  let cur: Turn | null = null;
  for (const m of messages) {
    if (m.role === "user") {
      cur = { key: m.id, user: m.display.text ?? "", parts: [], notices: [] };
      turns.push(cur);
    } else if (m.role === "assistant" && cur) {
      for (const p of m.display.parts ?? []) {
        if (p.kind === "text") cur.parts.push({ kind: "text", text: p.text });
        else cur.parts.push({ kind: "tool", id: p.id, name: p.name, title: p.title, args: p.args, result: results.get(p.id) });
      }
      if (m.display.notice) cur.notices.push(m.display.notice);
    }
  }
  return turns;
}

function apply(turn: Turn, e: StreamEvent): Turn {
  const parts = [...turn.parts];
  switch (e.event) {
    case "text": {
      const last = parts[parts.length - 1];
      if (last?.kind === "text") parts[parts.length - 1] = { kind: "text", text: last.text + e.data.delta };
      else parts.push({ kind: "text", text: e.data.delta });
      return { ...turn, parts };
    }
    case "tool_call":
      parts.push({ kind: "tool", ...e.data });
      return { ...turn, parts };
    case "tool_result":
      return { ...turn, parts: parts.map((p) => (p.kind === "tool" && p.id === e.data.call_id ? { ...p, result: e.data } : p)) };
    case "notice":
      return { ...turn, notices: [...turn.notices, e.data.message] };
    case "error":
      return { ...turn, error: e.data.message };
    default:
      return turn;
  }
}

/* Model output is rendered as Markdown without raw HTML or images, and links are only
 * followed when they point inside the product. */
const markdown: Components = {
  a: ({ href, children }) =>
    href && href.startsWith("/") && !href.startsWith("//") ? (
      <Link to={href} className="text-[var(--accent)] hover:underline">
        {children}
      </Link>
    ) : (
      <span title={href}>{children}</span>
    ),
};

function Prose({ text }: { text: string }) {
  return (
    <div className="prose-chat">
      <Markdown remarkPlugins={[remarkGfm]} skipHtml disallowedElements={["img"]} components={markdown}>
        {text}
      </Markdown>
    </div>
  );
}

function ToolStep({ part, ws }: { part: Extract<Part, { kind: "tool" }>; ws: string }) {
  const [open, setOpen] = useState(false);
  const r = part.result;
  const args = Object.entries(part.args).filter(([, v]) => v !== null && v !== undefined);
  return (
    <div className="my-2">
      <button
        onClick={() => setOpen((o) => !o)}
        className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--panel-2)] px-2.5 py-1 text-xs text-[var(--muted)] hover:text-[var(--text)]"
      >
        {!r ? <Loader2 className="size-3.5 animate-spin" /> : r.ok ? <Check className="size-3.5 text-[var(--ok)]" /> : <X className="size-3.5 text-[var(--err)]" />}
        <Wrench className="size-3.5" />
        <span className="font-medium text-[var(--text)]">{part.title}</span>
        {r && <span>· {r.duration_ms} ms</span>}
        <ChevronRight className={`size-3.5 transition ${open ? "rotate-90" : ""}`} />
      </button>
      {open && (
        <pre className="mt-1 overflow-x-auto rounded-md bg-[var(--panel-2)] p-2 font-mono text-[11px]">{args.length ? JSON.stringify(Object.fromEntries(args), null, 2) : "(no arguments)"}</pre>
      )}
      {r && !r.ok && <div className="mt-1 text-xs text-[var(--err)]">{r.error}</div>}
      {r?.card && <ResultCard card={r.card} ws={ws} />}
    </div>
  );
}

function TurnView({ turn, ws }: { turn: Turn; ws: string }) {
  const thinking = turn.live && turn.parts.length === 0 && !turn.error;
  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-[var(--accent)] px-4 py-2 text-sm whitespace-pre-wrap text-[var(--accent-fg)]">{turn.user}</div>
      </div>
      <div className="flex gap-3">
        <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-[var(--accent-soft)] text-[var(--accent)]">
          <Bot className="size-4" />
        </div>
        <div className="min-w-0 flex-1 text-sm">
          {thinking && (
            <div className="flex items-center gap-2 py-1 text-[var(--muted)]">
              <Loader2 className="size-4 animate-spin" /> Thinking…
            </div>
          )}
          {turn.parts.map((p, i) => (p.kind === "text" ? <Prose key={i} text={p.text} /> : <ToolStep key={p.id} part={p} ws={ws} />))}
          {turn.notices.map((n, i) => (
            <div key={i} className="mt-2 flex items-start gap-1.5 text-xs text-[var(--warn)]">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" /> {n}
            </div>
          ))}
          {turn.error && (
            <div className="mt-2 flex items-start gap-1.5 rounded-md border border-[var(--err)] px-3 py-2 text-xs text-[var(--err)]">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" /> {turn.error}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SettingsDialog({ ws, open, onClose }: { ws: string; open: boolean; onClose: () => void }) {
  const api = useApi();
  const qc = useQueryClient();
  const current = useQuery({ queryKey: ["assistant-settings", ws], queryFn: () => api.assistantSettings(ws), enabled: open });
  const [draft, setDraft] = useState<AssistantSettings | null>(null);
  const form = draft ?? current.data ?? null;
  const save = useMutation({
    mutationFn: (s: AssistantSettings) =>
      api.saveAssistantSettings(ws, {
        enabled: s.enabled,
        provider: s.provider,
        model: s.model,
        egress_mode: s.egress_mode,
        monthly_token_budget: s.monthly_token_budget,
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["assistant-status", ws] });
      await qc.invalidateQueries({ queryKey: ["assistant-settings", ws] });
      setDraft(null);
      onClose();
    },
  });
  const set = (patch: Partial<AssistantSettings>) => form && setDraft({ ...form, ...patch });
  return (
    <Dialog open={open} title="Assistant settings" onClose={onClose}>
      {!form ? (
        <Spinner />
      ) : (
        <div className="space-y-4">
          <ErrorBanner error={save.error ? errorMessage(save.error) : null} />
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> Assistant enabled for this workspace
          </label>
          <Field label="Data egress" hint={EGRESS[form.egress_mode].detail}>
            <Select
              value={form.egress_mode}
              onChange={(e) => {
                const egress = e.target.value as EgressMode;
                const provider = egress === "local_only" ? "ollama" : form.provider;
                set({ egress_mode: egress, provider, model: provider === form.provider ? form.model : (form.providers[provider]?.models[0] ?? "") });
              }}
            >
              <option value="external_redacted">Redacted before leaving the platform (recommended)</option>
              <option value="local_only">Local model only (nothing leaves)</option>
              <option value="external_allowed">External, unredacted (also enables MCP clients)</option>
            </Select>
          </Field>
          <Field label="Model provider" hint={form.providers[form.provider]?.reason ?? undefined}>
            <Select
              value={form.provider}
              onChange={(e) => {
                const provider = e.target.value as AssistantSettings["provider"];
                set({ provider, model: form.providers[provider]?.models[0] ?? "" });
              }}
            >
              {Object.entries(form.providers).map(([name, p]) => (
                <option key={name} value={name} disabled={form.egress_mode === "local_only" && p.external}>
                  {name === "anthropic" ? "Anthropic (Claude)" : "Local (Ollama)"}
                  {p.available ? "" : " — not configured"}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Model">
            <Select value={form.model} onChange={(e) => set({ model: e.target.value })}>
              {(form.providers[form.provider]?.models ?? []).map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Monthly token budget" hint="Leave empty for no limit.">
            <Input
              type="number"
              min={1000}
              step={1000}
              value={form.monthly_token_budget ?? ""}
              onChange={(e) => set({ monthly_token_budget: e.target.value ? Number(e.target.value) : null })}
            />
          </Field>
          <div className="flex justify-end gap-2">
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="primary" busy={save.isPending} onClick={() => save.mutate(form)}>
              Save
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

export function Assistant() {
  const api = useApi();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { conversationId } = useParams();
  const [params, setParams] = useSearchParams();
  const { workspaceId: ws, can } = useWorkspace();
  const [text, setText] = useState(() => params.get("q") ?? "");
  const [live, setLive] = useState<Turn | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  const status = useQuery({ queryKey: ["assistant-status", ws], queryFn: () => api.assistantStatus(ws), enabled: !!ws });
  const list = useQuery({ queryKey: ["conversations", ws], queryFn: () => api.conversations(ws), enabled: !!ws });
  const detail = useQuery({
    queryKey: ["conversation", ws, conversationId],
    queryFn: () => api.conversation(ws, conversationId!),
    enabled: !!ws && !!conversationId,
  });
  const turns = useMemo(() => toTurns(detail.data?.messages ?? []), [detail.data]);
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteConversation(ws, id),
    onSuccess: async (_, id) => {
      await qc.invalidateQueries({ queryKey: ["conversations", ws] });
      if (id === conversationId) navigate(`/w/${ws}/assistant`);
    },
  });

  useEffect(() => {
    if (params.get("q")) setParams({}, { replace: true });
  }, [params, setParams]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [turns.length, live]);

  const busy = live !== null;
  const available = status.data?.available ?? false;

  async function send(message: string) {
    const body = message.trim();
    if (!body || busy) return;
    setError(null);
    setText("");
    let cid = conversationId;
    try {
      if (!cid) {
        cid = (await api.createConversation(ws)).id;
        void qc.invalidateQueries({ queryKey: ["conversations", ws] });
        navigate(`/w/${ws}/assistant/${cid}`);
      }
    } catch (e) {
      setError(errorMessage(e));
      return;
    }
    const ctrl = new AbortController();
    abort.current = ctrl;
    setLive({ key: "live", user: body, parts: [], notices: [], live: true });
    try {
      await api.sendMessage(ws, cid, body, (e) => setLive((t) => (t ? apply(t, e) : t)), ctrl.signal);
    } catch (e) {
      if (!ctrl.signal.aborted) setError(errorMessage(e));
    } finally {
      abort.current = null;
      await qc.invalidateQueries({ queryKey: ["conversation", ws, cid] });
      await qc.invalidateQueries({ queryKey: ["conversations", ws] });
      void qc.invalidateQueries({ queryKey: ["assistant-status", ws] });
      setLive(null);
      input.current?.focus();
    }
  }

  const egress = status.data ? EGRESS[status.data.egress_mode] : null;
  let banner: ReactNode = null;
  if (status.data && !status.data.available) {
    banner = (
      <div className="flex items-start gap-2 rounded-md border border-[var(--warn)] px-3 py-2 text-sm text-[var(--warn)]">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" />
        <span>
          The assistant is unavailable: {status.data.reason}.{can("admin") ? " Open settings to configure it." : " Ask a workspace admin to configure it."}
        </span>
      </div>
    );
  }

  return (
    <div className="-my-2 grid h-[calc(100vh-7.5rem)] min-h-[480px] grid-cols-1 gap-4 lg:grid-cols-[260px_1fr]">
      <aside className="hidden flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--panel)] lg:flex">
        <div className="border-b border-[var(--border)] p-3">
          <Button variant="primary" className="w-full" onClick={() => navigate(`/w/${ws}/assistant`)}>
            <MessageSquarePlus className="size-4" /> New conversation
          </Button>
        </div>
        <nav className="flex-1 overflow-y-auto p-2">
          {list.data?.length === 0 && <div className="px-2 py-6 text-center text-xs text-[var(--muted)]">No conversations yet</div>}
          {list.data?.map((c) => (
            <div key={c.id} className={`group flex items-center rounded-md ${c.id === conversationId ? "bg-[var(--accent-soft)]" : "hover:bg-[var(--panel-2)]"}`}>
              <Link to={`/w/${ws}/assistant/${c.id}`} className="min-w-0 flex-1 px-2.5 py-2">
                <div className="truncate text-sm">{c.title}</div>
                <div className="text-[11px] text-[var(--muted)]">{relativeTime(c.updated_at)}</div>
              </Link>
              <button
                onClick={() => remove.mutate(c.id)}
                className="mr-1 hidden rounded p-1.5 text-[var(--muted)] group-hover:block hover:text-[var(--err)]"
                aria-label={`Delete ${c.title}`}
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))}
        </nav>
        <div className="border-t border-[var(--border)] p-3 text-[11px] leading-relaxed text-[var(--muted)]">
          Conversations are private to you and deleted after 30 days of inactivity.
        </div>
      </aside>

      <section className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--panel)]">
        <header className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] px-4 py-2.5">
          <Bot className="size-4 text-[var(--accent)]" />
          <h1 className="min-w-0 flex-1 truncate text-sm font-semibold">{detail.data?.title ?? "Assistant"}</h1>
          {status.data && (
            <>
              <Badge>{status.data.model}</Badge>
              {egress && (
                <span title={egress.detail}>
                  <Badge tone={egress.tone}>
                    {status.data.egress_mode === "external_allowed" ? <AlertTriangle className="size-3" /> : <Lock className="size-3" />} {egress.label}
                  </Badge>
                </span>
              )}
            </>
          )}
          {can("admin") && (
            <Button variant="ghost" onClick={() => setSettingsOpen(true)} aria-label="Assistant settings">
              <Settings2 className="size-4" />
            </Button>
          )}
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-5 md:px-8">
          <div className="mx-auto max-w-3xl space-y-8">
            {banner}
            {detail.isLoading && <Spinner />}
            {!conversationId && !live && (
              <div className="py-8 text-center">
                <div className="mx-auto mb-3 flex size-12 items-center justify-center rounded-2xl bg-[var(--accent-soft)] text-[var(--accent)]">
                  <Bot className="size-6" />
                </div>
                <h2 className="text-lg font-semibold">Ask about your estate</h2>
                <p className="mx-auto mt-1 max-w-lg text-sm text-[var(--muted)]">
                  The assistant uses the same inventory, cost, readiness and plan data you can see, with your permissions. It cannot change anything in your
                  clouds; drafts it creates still need human review.
                </p>
                {egress && (
                  <p className="mx-auto mt-3 flex max-w-lg items-start justify-center gap-1.5 text-xs text-[var(--muted)]">
                    <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-[var(--ok)]" /> {egress.detail}
                  </p>
                )}
                <div className="mx-auto mt-6 grid max-w-2xl gap-2 sm:grid-cols-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      disabled={!available}
                      onClick={() => void send(s)}
                      className="rounded-lg border border-[var(--border)] px-3 py-2.5 text-left text-sm hover:border-[var(--accent)] hover:bg-[var(--accent-soft)] disabled:opacity-50"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {turns.map((t) => (
              <TurnView key={t.key} turn={t} ws={ws} />
            ))}
            {live && <TurnView turn={live} ws={ws} />}
            <div ref={bottom} />
          </div>
        </div>

        <footer className="border-t border-[var(--border)] p-3">
          <div className="mx-auto max-w-3xl">
            <ErrorBanner error={error} onDismiss={() => setError(null)} />
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void send(text);
              }}
              className="flex items-end gap-2 rounded-xl border border-[var(--border)] bg-[var(--panel-2)] p-2 focus-within:border-[var(--accent)]"
            >
              <textarea
                ref={input}
                value={text}
                autoFocus
                rows={Math.min(6, Math.max(1, text.split("\n").length))}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    void send(text);
                  }
                }}
                maxLength={8000}
                placeholder={available ? "Ask about inventory, costs, readiness or plans…" : "Assistant unavailable"}
                disabled={!available}
                aria-label="Message"
                className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none"
              />
              {busy ? (
                <Button type="button" onClick={() => abort.current?.abort()} aria-label="Stop">
                  <Square className="size-4" />
                </Button>
              ) : (
                <Button type="submit" variant="primary" disabled={!text.trim() || !available} aria-label="Send">
                  <ArrowUp className="size-4" />
                </Button>
              )}
            </form>
            <div className="mt-1.5 text-center text-[11px] text-[var(--muted)]">
              Answers can be wrong; figures in cards come straight from the platform. Enter to send, Shift+Enter for a new line.
            </div>
          </div>
        </footer>
      </section>
      <SettingsDialog ws={ws} open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
