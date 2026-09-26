import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { Card, ErrorBanner, PageHeader, Spinner, StatusBadge, relativeTime } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useApi, useWorkspace } from "../lib/context";

export function Overview() {
  const api = useApi();
  const { workspaceId, workspace } = useWorkspace();
  const conns = useQuery({ queryKey: ["connections", workspaceId], queryFn: () => api.connections(workspaceId) });
  const audit = useQuery({ queryKey: ["audit", workspaceId, "recent"], queryFn: () => api.audit(workspaceId, { limit: 8 }) });

  const counts = { ok: 0, warning: 0, error: 0, untested: 0 };
  for (const c of conns.data ?? []) counts[c.status] += 1;

  return (
    <>
      <PageHeader title={workspace?.name ?? "Workspace"} subtitle="Read-only discovery, assessment and planning. No cloud resource is ever modified in this release." />
      <ErrorBanner error={conns.error ? errorMessage(conns.error) : null} />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {(
          [
            ["Connections", conns.data?.length ?? "—"],
            ["Healthy", counts.ok],
            ["Need attention", counts.warning + counts.error],
            ["Untested", counts.untested],
          ] as const
        ).map(([label, value]) => (
          <div key={label} className="rounded-lg border border-[var(--border)] bg-[var(--panel)] p-4">
            <div className="text-xs uppercase tracking-wide text-[var(--muted)]">{label}</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
          </div>
        ))}
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card title="Connections" actions={<Link className="text-sm text-[var(--accent)]" to="connections">Manage</Link>}>
          {conns.isLoading ? (
            <Spinner />
          ) : (conns.data ?? []).length === 0 ? (
            <p className="text-sm text-[var(--muted)]">
              No cloud connections yet. <Link className="text-[var(--accent)]" to="connections">Connect an AWS account</Link> to start discovery.
            </p>
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {conns.data!.map((c) => (
                <li key={c.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                  <span className="truncate">
                    <span className="font-medium">{c.name}</span> <span className="text-[var(--muted)]">· {c.provider.toUpperCase()}</span>
                  </span>
                  <span className="flex items-center gap-2 text-xs text-[var(--muted)]">
                    tested {relativeTime(c.last_tested_at)} <StatusBadge status={c.status} />
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Recent activity" actions={<Link className="text-sm text-[var(--accent)]" to="audit">Audit log</Link>}>
          {audit.isLoading ? (
            <Spinner />
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {(audit.data?.items ?? []).map((e) => (
                <li key={e.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                  <span className="truncate">
                    <span className="font-mono text-xs">{e.action}</span>{" "}
                    <span className="text-[var(--muted)]">by {e.actor_display ?? e.actor_type}</span>
                  </span>
                  <span className="text-xs whitespace-nowrap text-[var(--muted)]">{relativeTime(e.occurred_at)}</span>
                </li>
              ))}
              {audit.data?.items.length === 0 && <li className="py-2 text-sm text-[var(--muted)]">No activity yet.</li>}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
