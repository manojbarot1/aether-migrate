import { useQuery } from "@tanstack/react-query";
import { Activity, Cable, Cpu, HardDrive, MemoryStick, Radar, Server, ShieldCheck } from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { Link } from "react-router";
import { Card, ErrorBanner, LinkButton, PageHeader, Spinner, StatusBadge, relativeTime } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useApi, useWorkspace } from "../lib/context";
import { gib } from "../lib/format";
import type { Breakdown } from "../lib/types";

function Kpi({ icon: Icon, label, value, hint }: { icon: ComponentType<{ className?: string }>; label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-4 shadow-[var(--shadow)]">
      <div className="flex items-center gap-2 text-xs font-medium text-[var(--muted)]">
        <span className="flex size-7 items-center justify-center rounded-md bg-[var(--accent-soft)] text-[var(--accent)]">
          <Icon className="size-4" />
        </span>
        {label}
      </div>
      <div className="mt-3 text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-[var(--muted)]">{hint}</div>}
    </div>
  );
}

function Bars({ data, empty }: { data: Breakdown[]; empty: string }) {
  const max = Math.max(1, ...data.map((d) => d.count));
  if (data.length === 0) return <p className="text-sm text-[var(--muted)]">{empty}</p>;
  return (
    <ul className="space-y-2.5">
      {data.map((d) => (
        <li key={d.key} className="grid grid-cols-[8rem_1fr_2.5rem] items-center gap-3 text-sm">
          <span className="truncate text-[var(--muted)]" title={d.key}>
            {d.key}
          </span>
          <span className="h-2 overflow-hidden rounded-full bg-[var(--panel-2)]">
            <span className="block h-full rounded-full bg-[var(--accent)]" style={{ width: `${(d.count / max) * 100}%` }} />
          </span>
          <span className="text-right tabular-nums">{d.count}</span>
        </li>
      ))}
    </ul>
  );
}

export function Overview() {
  const api = useApi();
  const { workspaceId, workspace, can } = useWorkspace();
  const conns = useQuery({ queryKey: ["connections", workspaceId], queryFn: () => api.connections(workspaceId) });
  const summary = useQuery({ queryKey: ["inventory-summary", workspaceId], queryFn: () => api.inventorySummary(workspaceId) });
  const snaps = useQuery({ queryKey: ["snapshots", workspaceId], queryFn: () => api.snapshots(workspaceId) });
  const audit = useQuery({ queryKey: ["audit", workspaceId, "recent"], queryFn: () => api.audit(workspaceId, { limit: 8 }) });

  const s = summary.data;
  const vms = s?.resources_by_type.vm ?? 0;
  const healthy = (conns.data ?? []).filter((c) => c.status === "ok").length;
  const lastRun = snaps.data?.[0];
  const gaps = (snaps.data ?? [])
    .filter((x) => x.status !== "running")
    .slice(0, 1)
    .reduce((n, x) => n + (x.coverage ?? []).filter((c) => c.status !== "ok").length, 0);

  if (summary.isLoading || conns.isLoading) return <Spinner />;

  const noData = vms === 0;
  return (
    <>
      <PageHeader
        title={workspace?.name ?? "Overview"}
        subtitle="Read-only discovery, assessment and planning. No cloud resource is modified by this release."
        actions={
          can("analyst") && (
            <LinkButton to="discovery" variant="primary">
              <Radar className="size-4" /> Run discovery
            </LinkButton>
          )
        }
      />
      <ErrorBanner error={summary.error ? errorMessage(summary.error) : conns.error ? errorMessage(conns.error) : null} />

      {noData && (
        <Card className="mb-6">
          <div className="flex flex-col items-start gap-4 sm:flex-row sm:items-center">
            <div className="flex size-11 items-center justify-center rounded-lg bg-[var(--accent-soft)] text-[var(--accent)]">
              <Cable className="size-5" />
            </div>
            <div className="flex-1">
              <div className="font-medium">{(conns.data ?? []).length === 0 ? "Connect your first cloud account" : "Run your first discovery"}</div>
              <p className="text-sm text-[var(--muted)]">
                {(conns.data ?? []).length === 0
                  ? "Create a read-only AWS connection. The platform generates the least-privilege IAM policy for you."
                  : "Discovery builds a normalized inventory of VMs, disks, networks and security rules."}
              </p>
            </div>
            <LinkButton to={(conns.data ?? []).length === 0 ? "connections" : "discovery"} variant="primary">
              {(conns.data ?? []).length === 0 ? "Add connection" : "Open discovery"}
            </LinkButton>
          </div>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi icon={Server} label="Virtual machines" value={vms} hint={`${s?.vms_by_region.length ?? 0} regions`} />
        <Kpi icon={Cpu} label="vCPU" value={s?.total_vcpu ?? 0} hint="allocated" />
        <Kpi icon={MemoryStick} label="Memory" value={gib(s?.total_memory_mib ?? 0)} hint="allocated" />
        <Kpi icon={HardDrive} label="Disks" value={s?.resources_by_type.disk ?? 0} hint={`${s?.resources_by_type.load_balancer ?? 0} load balancers`} />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card title="VMs by region" className="lg:col-span-2">
          <Bars data={(s?.vms_by_region ?? []).map((r) => ({ key: r.region, count: r.count }))} empty="No machines discovered yet." />
        </Card>
        <Card title="Platform">
          <div className="space-y-4">
            <div>
              <div className="mb-2 text-xs font-medium text-[var(--muted)]">Operating system</div>
              <Bars data={s?.vms_by_os ?? []} empty="—" />
            </div>
            <div>
              <div className="mb-2 text-xs font-medium text-[var(--muted)]">Architecture</div>
              <Bars data={s?.vms_by_arch ?? []} empty="—" />
            </div>
          </div>
        </Card>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card title="Connections" actions={<Link className="text-sm text-[var(--accent)]" to="connections">Manage</Link>}>
          <div className="mb-3 flex items-center gap-2 text-sm">
            <ShieldCheck className="size-4 text-[var(--ok)]" />
            {healthy} of {(conns.data ?? []).length} healthy
          </div>
          <ul className="divide-y divide-[var(--border)]">
            {(conns.data ?? []).map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <span className="truncate font-medium">{c.name}</span>
                <StatusBadge status={c.status} />
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Latest discovery" actions={<Link className="text-sm text-[var(--accent)]" to="discovery">All runs</Link>}>
          {lastRun ? (
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-[var(--muted)]">Status</dt>
                <dd>
                  <StatusBadge status={lastRun.status} />
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-[var(--muted)]">Started</dt>
                <dd>{relativeTime(lastRun.started_at)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-[var(--muted)]">Regions</dt>
                <dd className="tabular-nums">{lastRun.regions?.length ?? "—"}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-[var(--muted)]">Resources</dt>
                <dd className="tabular-nums">{Object.values(lastRun.stats?.resources ?? {}).reduce((a, b) => a + b, 0)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-[var(--muted)]">Coverage gaps</dt>
                <dd className={gaps ? "text-[var(--warn)]" : ""}>{gaps}</dd>
              </div>
            </dl>
          ) : (
            <p className="text-sm text-[var(--muted)]">No discovery has run yet.</p>
          )}
        </Card>
        <Card title="Recent activity" actions={<Link className="text-sm text-[var(--accent)]" to="audit">Audit log</Link>}>
          <ul className="space-y-2.5">
            {(audit.data?.items ?? []).map((e) => (
              <li key={e.id} className="flex items-start gap-2.5 text-sm">
                <Activity className="mt-0.5 size-3.5 shrink-0 text-[var(--muted)]" />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-mono text-xs">{e.action}</div>
                  <div className="truncate text-xs text-[var(--muted)]">
                    {e.actor_display ?? e.actor_type} · {relativeTime(e.occurred_at)}
                  </div>
                </div>
              </li>
            ))}
            {audit.data?.items.length === 0 && <li className="text-sm text-[var(--muted)]">No activity yet.</li>}
          </ul>
        </Card>
      </div>
    </>
  );
}
