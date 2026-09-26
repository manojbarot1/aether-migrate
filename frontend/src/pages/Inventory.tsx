import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Server } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Badge, Button, Card, EmptyState, ErrorBanner, Input, PageHeader, Select, Spinner, StatusBadge, Table, relativeTime } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useApi, useWorkspace } from "../lib/context";

const PAGE = 50;

export const gib = (mib: number | null | undefined) => (mib == null ? "—" : `${+(mib / 1024).toFixed(1)} GiB`);

export function Inventory() {
  const api = useApi();
  const { workspaceId } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const [draft, setDraft] = useState(params.get("q") ?? "");

  const filters = {
    type: "vm",
    q: params.get("q") ?? undefined,
    region: params.get("region") ?? undefined,
    os_family: params.get("os_family") ?? undefined,
    cpu_arch: params.get("cpu_arch") ?? undefined,
    status: params.get("status") ?? undefined,
    min_vcpu: params.get("min_vcpu") ?? undefined,
    min_memory_gib: params.get("min_memory_gib") ?? undefined,
    tag: params.get("tag") ?? undefined,
    sort: params.get("sort") ?? "name",
    order: params.get("order") ?? "asc",
    limit: PAGE,
    offset: Number(params.get("offset") ?? 0),
  };
  const set = (k: string, v: string | undefined) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    if (k !== "offset") next.delete("offset");
    setParams(next, { replace: true });
  };

  const summary = useQuery({ queryKey: ["inventory-summary", workspaceId], queryFn: () => api.inventorySummary(workspaceId) });
  const snapshots = useQuery({ queryKey: ["snapshots", workspaceId], queryFn: () => api.snapshots(workspaceId) });
  const page = useQuery({
    queryKey: ["resources", workspaceId, filters],
    queryFn: () => api.resources(workspaceId, filters),
    placeholderData: keepPreviousData,
  });

  const latest = useMemo(() => {
    const seen = new Set<string>();
    return (snapshots.data ?? []).filter((s) => {
      if (s.status === "running" || s.status === "failed" || seen.has(s.connection_id)) return false;
      seen.add(s.connection_id);
      return true;
    });
  }, [snapshots.data]);
  const gaps = latest.reduce((n, s) => n + (s.coverage ?? []).filter((c) => c.status !== "ok").length, 0);
  const oldest = latest.map((s) => s.finished_at).filter(Boolean).sort()[0] ?? null;

  const sortHeader = (key: string, label: string) => {
    const active = filters.sort === key;
    return (
      <button
        className="inline-flex items-center gap-1 uppercase"
        onClick={() => {
          const next = new URLSearchParams(params);
          next.set("sort", key);
          next.set("order", active && filters.order === "asc" ? "desc" : "asc");
          setParams(next, { replace: true });
        }}
      >
        {label}
        {active && (filters.order === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
      </button>
    );
  };

  const total = page.data?.total ?? 0;
  return (
    <>
      <PageHeader
        title="Inventory"
        subtitle={
          latest.length === 0 ? (
            "No completed discovery yet."
          ) : (
            <>
              Latest snapshot of {latest.length} connection{latest.length === 1 ? "" : "s"} · oldest data {relativeTime(oldest)}
              {gaps > 0 && (
                <>
                  {" · "}
                  <Link to="../discovery" relative="path" className="text-[var(--warn)]">
                    {gaps} coverage gap{gaps === 1 ? "" : "s"}
                  </Link>
                </>
              )}
            </>
          )
        }
      />
      {summary.data && (
        <div className="mb-4 grid gap-3 sm:grid-cols-4">
          {(
            [
              ["VMs", summary.data.resources_by_type.vm ?? 0],
              ["vCPU", summary.data.total_vcpu],
              ["Memory", gib(summary.data.total_memory_mib)],
              ["Disks", summary.data.resources_by_type.disk ?? 0],
            ] as const
          ).map(([label, v]) => (
            <div key={label} className="rounded-lg border border-[var(--border)] bg-[var(--panel)] p-3">
              <div className="text-xs uppercase tracking-wide text-[var(--muted)]">{label}</div>
              <div className="text-xl font-semibold tabular-nums">{v}</div>
            </div>
          ))}
        </div>
      )}
      <ErrorBanner error={page.error ? errorMessage(page.error) : null} />
      <Card>
        <form
          className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-7"
          onSubmit={(e) => {
            e.preventDefault();
            set("q", draft.trim() || undefined);
          }}
        >
          <Input className="lg:col-span-2" placeholder="Search name or instance id" value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Search" />
          <Select aria-label="Region" value={filters.region ?? ""} onChange={(e) => set("region", e.target.value || undefined)}>
            <option value="">All regions</option>
            {(summary.data?.vms_by_region ?? []).map((r) => (
              <option key={r.region} value={r.region}>
                {r.region} ({r.count})
              </option>
            ))}
          </Select>
          <Select aria-label="OS" value={filters.os_family ?? ""} onChange={(e) => set("os_family", e.target.value || undefined)}>
            <option value="">Any OS</option>
            <option value="linux">Linux</option>
            <option value="windows">Windows</option>
            <option value="unknown">Unknown</option>
          </Select>
          <Select aria-label="Architecture" value={filters.cpu_arch ?? ""} onChange={(e) => set("cpu_arch", e.target.value || undefined)}>
            <option value="">Any arch</option>
            <option value="x86_64">x86_64</option>
            <option value="arm64">arm64</option>
          </Select>
          <Input type="number" min={0} placeholder="Min vCPU" aria-label="Minimum vCPU" defaultValue={filters.min_vcpu} onBlur={(e) => set("min_vcpu", e.target.value || undefined)} />
          <Input type="number" min={0} step="0.5" placeholder="Min GiB" aria-label="Minimum memory GiB" defaultValue={filters.min_memory_gib} onBlur={(e) => set("min_memory_gib", e.target.value || undefined)} />
          <Input className="lg:col-span-2" placeholder="Tag: key or key=value" aria-label="Tag filter" defaultValue={filters.tag} onBlur={(e) => set("tag", e.target.value.trim() || undefined)} />
          <Select aria-label="Status" value={filters.status ?? ""} onChange={(e) => set("status", e.target.value || undefined)}>
            <option value="">Any state</option>
            <option value="running">Running</option>
            <option value="stopped">Stopped</option>
          </Select>
          <div className="flex gap-2 lg:col-span-4">
            <Button type="submit" variant="primary">
              Apply
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setDraft("");
                setParams(new URLSearchParams(), { replace: true });
              }}
            >
              Reset
            </Button>
            <span className="ml-auto self-center text-sm text-[var(--muted)]">{total} VM{total === 1 ? "" : "s"}</span>
          </div>
        </form>
        {page.isLoading ? (
          <Spinner />
        ) : total === 0 ? (
          <EmptyState icon={<Server className="size-8" />} title="No VMs match">
            {latest.length === 0 ? (
              <>
                Run discovery from <Link className="text-[var(--accent)]" to="../connections" relative="path">Connections</Link>.
              </>
            ) : (
              "Adjust or reset the filters."
            )}
          </EmptyState>
        ) : (
          <>
            <Table head={[sortHeader("name", "Name"), "Type", sortHeader("vcpu", "vCPU"), sortHeader("memory", "Memory"), "OS / arch", sortHeader("region", "Region"), sortHeader("status", "State")]}>
              {page.data!.items.map((r) => (
                <tr key={r.id} className="border-b border-[var(--border)] hover:bg-[var(--panel-2)]">
                  <td className="px-3 py-2">
                    <Link to={r.id} className="font-medium hover:text-[var(--accent)]">
                      {r.name ?? r.native_id}
                    </Link>
                    <div className="max-w-48 truncate font-mono text-xs text-[var(--muted)]" title={r.native_id}>{r.native_id}</div>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{r.source_sku ?? "—"}</td>
                  <td className="px-3 py-2 tabular-nums">{r.vcpu ?? "—"}</td>
                  <td className="px-3 py-2 tabular-nums">{gib(r.memory_mib)}</td>
                  <td className="px-3 py-2">
                    <span className="capitalize">{r.os_family ?? "—"}</span> <Badge>{r.cpu_arch ?? "?"}</Badge>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap text-[var(--muted)]">{r.region}</td>
                  <td className="px-3 py-2">
                    <StatusBadge status={r.status ?? "unknown"} />
                  </td>
                </tr>
              ))}
            </Table>
            <div className="mt-3 flex items-center justify-between text-sm text-[var(--muted)]">
              <span>
                {filters.offset + 1}–{Math.min(filters.offset + PAGE, total)} of {total}
              </span>
              <div className="flex gap-2">
                <Button disabled={filters.offset === 0} onClick={() => set("offset", String(Math.max(0, filters.offset - PAGE)))}>
                  Previous
                </Button>
                <Button disabled={filters.offset + PAGE >= total} onClick={() => set("offset", String(filters.offset + PAGE))}>
                  Next
                </Button>
              </div>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
