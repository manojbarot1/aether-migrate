import { useQuery } from "@tanstack/react-query";
import { Bot, CornerDownLeft, Search, Server } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ComponentType } from "react";
import { useNavigate } from "react-router";
import { useApi } from "../lib/context";

export interface PaletteLink {
  label: string;
  to: string;
  icon: ComponentType<{ className?: string }>;
  group: string;
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** ⌘K / Ctrl+K: jump to any page or machine by name or instance id. */
export function CommandPalette({ workspaceId, links, assistant }: { workspaceId?: string; links: PaletteLink[]; assistant?: string }) {
  const api = useApi();
  const navigate = useNavigate();
  const ref = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const term = useDebounced(q.trim(), 150);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("aether:palette", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("aether:palette", onOpen);
    };
  }, []);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  const close = () => {
    setOpen(false);
    setQ("");
    setCursor(0);
  };

  const vms = useQuery({
    queryKey: ["palette", workspaceId, term],
    queryFn: () => api.resources(workspaceId!, { type: "vm", q: term, limit: 8 }),
    enabled: open && !!workspaceId && term.length >= 2,
    staleTime: 30_000,
  });

  const items = useMemo(() => {
    const lower = term.toLowerCase();
    const pages = links.filter((l) => !lower || l.label.toLowerCase().includes(lower)).map((l) => ({ ...l, key: l.to }));
    const machines = (vms.data?.items ?? []).map((r) => ({
      key: r.id,
      label: r.name ?? r.native_id,
      hint: `${r.native_id} · ${r.source_sku ?? ""} · ${r.region}`,
      to: `/w/${workspaceId}/inventory/${r.id}`,
      icon: Server,
      group: "Machines",
    }));
    // A question-shaped query can go straight to the assistant.
    const ask =
      assistant && term.length >= 3
        ? [{ key: "ask", label: `Ask the assistant: “${term}”`, to: `${assistant}?q=${encodeURIComponent(term)}`, icon: Bot, group: "Assistant" }]
        : [];
    return [...pages, ...machines, ...ask] as { key: string; label: string; to: string; icon: PaletteLink["icon"]; group: string; hint?: string }[];
  }, [links, vms.data, term, workspaceId, assistant]);

  const go = (to: string) => {
    close();
    navigate(to);
  };

  let lastGroup = "";
  return (
    <dialog
      ref={ref}
      onClose={close}
      onClick={(e) => e.target === ref.current && close()}
      className="mx-auto mt-[12vh] w-[calc(100%-2rem)] max-w-xl rounded-xl border border-[var(--border)] bg-[var(--panel)] p-0 text-[var(--text)] shadow-2xl backdrop:bg-black/40"
    >
      {open && (
        <div>
          <div className="flex items-center gap-2 border-b border-[var(--border)] px-4">
            <Search className="size-4 text-[var(--muted)]" aria-hidden />
            <input
              autoFocus
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setCursor(0);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setCursor((c) => Math.min(c + 1, items.length - 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setCursor((c) => Math.max(c - 1, 0));
                } else if (e.key === "Enter" && items[cursor]) {
                  go(items[cursor].to);
                }
              }}
              placeholder={workspaceId ? "Jump to a page, or search machines by name or instance id…" : "Jump to…"}
              className="h-12 w-full bg-transparent text-sm outline-none placeholder:text-[var(--muted)] focus-visible:outline-none"
              aria-label="Command search"
            />
            <kbd className="rounded border border-[var(--border)] px-1.5 text-[10px] text-[var(--muted)]">Esc</kbd>
          </div>
          <ul className="max-h-[50vh] overflow-y-auto p-2" role="listbox">
            {items.length === 0 && <li className="px-3 py-6 text-center text-sm text-[var(--muted)]">{vms.isFetching ? "Searching…" : "No matches"}</li>}
            {items.map((it, i) => {
              const header = it.group !== lastGroup ? it.group : null;
              lastGroup = it.group;
              const Icon = it.icon;
              return (
                <li key={it.key}>
                  {header && <div className="px-3 pt-2 pb-1 text-[11px] font-medium tracking-wide text-[var(--muted)] uppercase">{header}</div>}
                  <button
                    role="option"
                    aria-selected={i === cursor}
                    onMouseEnter={() => setCursor(i)}
                    onClick={() => go(it.to)}
                    className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm ${i === cursor ? "bg-[var(--accent-soft)]" : ""}`}
                  >
                    <Icon className="size-4 shrink-0 text-[var(--muted)]" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{it.label}</span>
                      {it.hint && <span className="block truncate font-mono text-[11px] text-[var(--muted)]">{it.hint}</span>}
                    </span>
                    {i === cursor && <CornerDownLeft className="size-3.5 text-[var(--muted)]" />}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </dialog>
  );
}
