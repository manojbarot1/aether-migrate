import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Radar } from "lucide-react";
import { Fragment, useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorBanner, PageHeader, Select, Spinner, StatusBadge, Table, relativeTime } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useApi, useWorkspace } from "../lib/context";
import type { Snapshot } from "../lib/types";

function duration(s: Snapshot): string {
  if (!s.finished_at) return "running…";
  const sec = Math.round((new Date(s.finished_at).getTime() - new Date(s.started_at).getTime()) / 1000);
  return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

export function Discovery() {
  const api = useApi();
  const qc = useQueryClient();
  const { workspaceId, can } = useWorkspace();
  const [target, setTarget] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const conns = useQuery({ queryKey: ["connections", workspaceId], queryFn: () => api.connections(workspaceId) });
  const snaps = useQuery({
    queryKey: ["snapshots", workspaceId],
    queryFn: () => api.snapshots(workspaceId),
    refetchInterval: (q) => ((q.state.data ?? []).some((s) => s.status === "running") ? 3000 : false),
  });
  const run = useMutation({
    mutationFn: (connectionId: string) => api.discover(workspaceId, connectionId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["snapshots", workspaceId] }),
  });
  const names = new Map((conns.data ?? []).map((c) => [c.id, c.name]));

  return (
    <>
      <PageHeader
        title="Discovery runs"
        subtitle="Each run is an immutable snapshot. Coverage shows exactly which regions and resource types were read, and why any were not."
        actions={
          can("analyst") && (
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (target) run.mutate(target);
              }}
            >
              <Select aria-label="Connection" value={target} onChange={(e) => setTarget(e.target.value)}>
                <option value="">Select connection…</option>
                {(conns.data ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
              <Button type="submit" variant="primary" disabled={!target} busy={run.isPending}>
                <Radar className="size-4" /> Run discovery
              </Button>
            </form>
          )
        }
      />
      <ErrorBanner error={run.error ? errorMessage(run.error) : snaps.error ? errorMessage(snaps.error) : null} />
      <Card>
        {snaps.isLoading ? (
          <Spinner />
        ) : (snaps.data ?? []).length === 0 ? (
          <EmptyState icon={<Radar className="size-8" />} title="No discovery runs yet" />
        ) : (
          <Table head={["Started", "Connection", "Status", "Duration", "Regions", "VMs", "Resources", "Gaps"]}>
            {snaps.data!.map((s) => {
              const gaps = (s.coverage ?? []).filter((c) => c.status !== "ok");
              const res = s.stats?.resources ?? {};
              const total = Object.values(res).reduce((a, b) => a + b, 0);
              return (
                <Fragment key={s.id}>
                  <tr className="cursor-pointer border-b border-[var(--border)] hover:bg-[var(--panel-2)]" onClick={() => setOpen(open === s.id ? null : s.id)}>
                    <td className="px-3 py-2 whitespace-nowrap">{relativeTime(s.started_at)}</td>
                    <td className="px-3 py-2">{names.get(s.connection_id) ?? s.connection_id.slice(0, 8)}</td>
                    <td className="px-3 py-2">
                      <StatusBadge status={s.status === "complete" ? "ok" : s.status === "partial" ? "warning" : s.status === "failed" ? "error" : s.status} />
                    </td>
                    <td className="px-3 py-2 tabular-nums">{duration(s)}</td>
                    <td className="px-3 py-2 tabular-nums">{s.regions?.length ?? "—"}</td>
                    <td className="px-3 py-2 tabular-nums">{res.vm ?? "—"}</td>
                    <td className="px-3 py-2 tabular-nums">{s.stats ? total : "—"}</td>
                    <td className="px-3 py-2">{gaps.length ? <Badge tone="warn">{gaps.length}</Badge> : s.coverage ? <Badge tone="ok">none</Badge> : "—"}</td>
                  </tr>
                  {open === s.id && (
                    <tr className="border-b border-[var(--border)] bg-[var(--panel-2)]">
                      <td colSpan={8} className="px-4 py-3 text-sm">
                        {s.error && <p className="mb-2 text-[var(--err)]">Run failed: {s.error}</p>}
                        {Object.keys(res).length > 0 && (
                          <p className="mb-2 text-[var(--muted)]">
                            {Object.entries(res)
                              .map(([k, v]) => `${v} ${k.replace("_", " ")}${v === 1 ? "" : "s"}`)
                              .join(" · ")}{" "}
                            · {s.stats?.cloud_calls ?? 0} cloud API calls
                          </p>
                        )}
                        {gaps.length === 0 ? (
                          <p className="text-[var(--muted)]">{s.coverage ? "Every region and resource type was read successfully." : "Coverage not available yet."}</p>
                        ) : (
                          <Table head={["Region", "Resource type", "Status", "Detail"]}>
                            {gaps.map((g, i) => (
                              <tr key={i} className="border-b border-[var(--border)]">
                                <td className="px-3 py-1.5">{g.region}</td>
                                <td className="px-3 py-1.5 font-mono text-xs">{g.kind}</td>
                                <td className="px-3 py-1.5">
                                  <StatusBadge status={g.status === "denied" ? "denied" : "warning"} />
                                </td>
                                <td className="px-3 py-1.5 text-[var(--muted)]">
                                  {g.detail}
                                  {g.status === "denied" && " — add the permission from the connection's setup policy"}
                                </td>
                              </tr>
                            ))}
                          </Table>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </Table>
        )}
      </Card>
    </>
  );
}
