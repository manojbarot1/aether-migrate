import { ArrowUpRight, Braces } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import type { ToolCard } from "../lib/types";
import { Badge, StatusBadge, relativeTime } from "./ui";

/* Tool results rendered as cards. Card data comes from our own API (never from the
 * model), so numbers shown here are exact; the model's prose only summarises them. */

type Row = Record<string, unknown>;
const rows = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : []);
const num = (v: unknown): number | null => (typeof v === "number" ? v : null);
const str = (v: unknown): string => (v == null ? "—" : String(v));
const obj = (v: unknown): Row => (v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : {});

function money(usd: unknown, fx = 1, currency = "USD"): string {
  const n = num(usd);
  if (n == null) return "—";
  return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(n * fx);
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-md bg-[var(--panel-2)] px-3 py-2">
      <div className="text-[11px] tracking-wide text-[var(--muted)] uppercase">{label}</div>
      <div className="text-base font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function MiniTable({ head, body }: { head: string[]; body: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-[10px] tracking-wide text-[var(--muted)] uppercase">
            {head.map((h) => (
              <th key={h} className="px-2 py-1 font-semibold">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((r, i) => (
            <tr key={i} className="border-t border-[var(--border)]">
              {r.map((c, j) => (
                <td key={j} className="px-2 py-1.5 whitespace-nowrap">
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VmTable({ card, ws }: { card: ToolCard; ws: string }) {
  const items = rows(card.data.items);
  const shown = items.slice(0, 10);
  return (
    <>
      <MiniTable
        head={["Name", "Region", "Status", "vCPU", "Memory", "OS", "Size"]}
        body={shown.map((v) => [
          <Link key="n" to={`/w/${ws}/inventory/${str(v.id)}`} className="font-medium text-[var(--accent)] hover:underline">
            {str(v.name ?? v.native_id)}
          </Link>,
          str(v.region),
          str(v.status),
          str(v.vcpu),
          v.memory_gib == null ? "—" : `${str(v.memory_gib)} GiB`,
          str(v.os_family),
          <span key="s" className="font-mono">
            {str(v.source_sku)}
          </span>,
        ])}
      />
      {items.length > shown.length && <div className="mt-1 text-xs text-[var(--muted)]">+{items.length - shown.length} more in the inventory</div>}
      {card.data.as_of ? <div className="mt-1 text-xs text-[var(--muted)]">Inventory as of {relativeTime(str(card.data.as_of))}</div> : null}
    </>
  );
}

function InventorySummary({ card }: { card: ToolCard }) {
  const d = card.data;
  const byType = obj(d.resources_by_type);
  return (
    <>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="VMs" value={str(byType.vm ?? 0)} />
        <Stat label="vCPU" value={str(d.total_vcpu)} />
        <Stat label="Memory" value={`${Math.round((num(d.total_memory_mib) ?? 0) / 1024)} GiB`} />
        <Stat label="Regions" value={rows(d.vms_by_region).length} />
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {rows(d.vms_by_os).map((b) => (
          <Badge key={str(b.key)}>
            {str(b.key)} · {str(b.count)}
          </Badge>
        ))}
        {rows(d.vms_by_region).map((b) => (
          <Badge key={str(b.region)} tone="accent">
            {str(b.region)} · {str(b.count)}
          </Badge>
        ))}
      </div>
    </>
  );
}

function DiscoveryStatus({ card }: { card: ToolCard }) {
  return (
    <MiniTable
      head={["Connection", "Last run", "Inventory as of", "Regions", "Gaps"]}
      body={rows(card.data.items).map((c) => {
        const run = obj(c.latest_run);
        return [
          str(c.connection),
          run.status ? <StatusBadge key="s" status={str(run.status)} /> : "never",
          c.inventory_as_of ? relativeTime(str(c.inventory_as_of)) : "—",
          str(c.regions),
          num(c.coverage_gaps) ? <Badge key="g" tone="warn">{str(c.coverage_gaps)}</Badge> : "0",
        ];
      })}
    />
  );
}

function CostCompare({ card }: { card: ToolCard }) {
  const d = card.data;
  const fx = num(d.fx_per_usd) ?? 1;
  const cur = str(d.currency);
  const totals = obj(d.totals_usd);
  const src = obj(totals.source_monthly_usd);
  const tgt = obj(totals.target_monthly_usd);
  return (
    <>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Source / month" value={money(src.on_demand, fx, cur)} />
        <Stat label="Azure on-demand" value={money(tgt.on_demand, fx, cur)} />
        <Stat label="Azure 3-yr reserved" value={money(tgt.reserved_3y, fx, cur)} />
        <Stat label="One-time" value={money(totals.one_time_usd, fx, cur)} />
      </div>
      <MiniTable
        head={["VM", "Source size", "Target size", "Target / month"]}
        body={rows(d.vms)
          .slice(0, 10)
          .map((v) => [
            str(v.name),
            <span key="a" className="font-mono">
              {str(v.source_sku)}
            </span>,
            v.target_sku ? (
              <span key="b" className="font-mono">
                {str(v.target_sku)}
              </span>
            ) : (
              <Badge key="b" tone="err">
                no fit
              </Badge>
            ),
            money(obj(v.target_monthly_usd).on_demand, fx, cur),
          ])}
      />
      <div className="mt-1 text-xs text-[var(--muted)]">
        {d.source_prices_available ? "" : "Source prices unavailable for some machines. "}
        Target prices {d.target_prices_stale ? "are stale" : "as of " + relativeTime(str(d.target_prices_as_of))}; {cur} at {fx} per USD.
      </div>
    </>
  );
}

function Assessment({ card }: { card: ToolCard }) {
  const s = obj(card.data.summary);
  const r = obj(s.readiness);
  const issues = Array.isArray(s.top_issues) ? (s.top_issues as [string, number][]) : [];
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="ok">ready · {str(r.ready ?? 0)}</Badge>
        <Badge tone="warn">with changes · {str(r.ready_with_changes ?? 0)}</Badge>
        <Badge tone="err">blocked · {str(r.blocked ?? 0)}</Badge>
        <span className="text-xs text-[var(--muted)]">average score {str(s.average_score)}</span>
      </div>
      {issues.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {issues.map(([rule, count]) => (
            <Badge key={rule}>
              <span className="font-mono">{rule}</span> · {count}
            </Badge>
          ))}
        </div>
      )}
    </>
  );
}

function Plan({ card, ws }: { card: ToolCard; ws: string }) {
  const d = card.data;
  if (d.items) {
    return (
      <MiniTable
        head={["Plan", "Version", "Status", "VMs", "Waves"]}
        body={rows(d.items).map((p) => [
          <Link key="p" to={`/w/${ws}/plans/${str(p.plan_id)}`} className="font-medium text-[var(--accent)] hover:underline">
            {str(p.name)}
          </Link>,
          `v${str(p.version)}`,
          <StatusBadge key="s" status={str(p.status)} />,
          str(p.vms),
          str(p.waves),
        ])}
      />
    );
  }
  const t = obj(d.totals);
  const monthly = obj(t.target_monthly_usd);
  return (
    <>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Status" value={<StatusBadge status={str(d.status)} />} />
        <Stat label="VMs / waves" value={`${str(t.vms)} / ${str(t.waves)}`} />
        <Stat label="Target / month" value={money(monthly.on_demand)} />
        <Stat label="One-time" value={money(t.one_time_usd)} />
      </div>
      <div className="mt-2 text-xs text-[var(--muted)]">
        Version {str(d.version)} · hash <span className="font-mono">{str(d.content_hash)}</span>
        {d.status === "draft" ? " · drafts need submission and a separate approver" : ""}
      </div>
    </>
  );
}

function Generic({ card }: { card: ToolCard }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button onClick={() => setOpen((o) => !o)} className="inline-flex items-center gap-1 text-xs text-[var(--muted)] hover:text-[var(--text)]">
        <Braces className="size-3.5" /> {open ? "Hide" : "Show"} data
      </button>
      {open && <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-[var(--panel-2)] p-2 font-mono text-[11px]">{JSON.stringify(card.data, null, 2)}</pre>}
    </div>
  );
}

export function ResultCard({ card, ws }: { card: ToolCard; ws: string }) {
  let body: ReactNode;
  switch (card.kind) {
    case "vm_table":
      body = <VmTable card={card} ws={ws} />;
      break;
    case "inventory_summary":
      body = <InventorySummary card={card} />;
      break;
    case "discovery_status":
      body = <DiscoveryStatus card={card} />;
      break;
    case "cost_compare":
      body = <CostCompare card={card} />;
      break;
    case "assessment":
      body = <Assessment card={card} />;
      break;
    case "plan":
    case "plan_list":
      body = <Plan card={card} ws={ws} />;
      break;
    case "topology": {
      const counts = obj(card.data.counts);
      body = (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(counts).map(([k, v]) => (
            <Badge key={k}>
              {k.replace("_", " ")} · {str(v)}
            </Badge>
          ))}
        </div>
      );
      break;
    }
    case "connections":
      body = (
        <MiniTable
          head={["Connection", "Provider", "Status", "Last test"]}
          body={rows(card.data.items).map((c) => [str(c.name), str(c.provider), <StatusBadge key="s" status={str(c.status)} />, relativeTime(c.last_tested_at ? str(c.last_tested_at) : null)])}
        />
      );
      break;
    default:
      body = <Generic card={card} />;
  }
  // Links come from our own API and are always relative, in-app routes.
  const link = card.link && card.link.startsWith("/") && !card.link.startsWith("//") ? card.link : null;
  return (
    <div className="mt-2 rounded-lg border border-[var(--border)] bg-[var(--panel)] p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="text-sm font-semibold">{card.title}</div>
        {link && (
          <Link to={link} className="inline-flex items-center gap-0.5 text-xs text-[var(--accent)] hover:underline">
            Open <ArrowUpRight className="size-3.5" />
          </Link>
        )}
      </div>
      {body}
    </div>
  );
}
