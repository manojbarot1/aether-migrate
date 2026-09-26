import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronDown, ChevronRight, ClipboardCheck, Info, OctagonAlert, TriangleAlert } from "lucide-react";
import { Fragment, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { Badge, Button, Card, Dialog, EmptyState, ErrorBanner, Field, Input, PageHeader, Select, Spinner, Table, relativeTime } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useApi, useWorkspace } from "../lib/context";
import type { AssessedVm, Finding, ReadinessState, Severity } from "../lib/types";

const READINESS: Record<ReadinessState, { label: string; tone: "ok" | "warn" | "err" }> = {
  ready: { label: "Ready", tone: "ok" },
  ready_with_changes: { label: "Ready with changes", tone: "warn" },
  blocked: { label: "Blocked", tone: "err" },
};

const SEV_ICON: Record<Severity, typeof Info> = { blocker: OctagonAlert, warning: TriangleAlert, info: Info };
const SEV_COLOR: Record<Severity, string> = { blocker: "text-[var(--err)]", warning: "text-[var(--warn)]", info: "text-[var(--muted)]" };

function ReadinessBadge({ r }: { r: ReadinessState }) {
  return <Badge tone={READINESS[r].tone}>{READINESS[r].label}</Badge>;
}

function Evidence({ e }: { e: Record<string, unknown> }) {
  const entries = Object.entries(e).filter(([, v]) => v !== null && v !== undefined && !(Array.isArray(v) && v.length === 0));
  if (entries.length === 0) return null;
  return (
    <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 text-xs">
      {entries.map(([k, v]) => (
        <Fragment key={k}>
          <dt className="text-[var(--muted)]">{k.replace(/_/g, " ")}</dt>
          <dd className="font-mono break-all">{Array.isArray(v) ? v.join(", ") : String(v)}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

function FindingRow({ f, vm, onAck, onRevoke, canAck }: { f: Finding; vm: AssessedVm; onAck: () => void; onRevoke: () => void; canAck: boolean }) {
  const Icon = f.acknowledged ? CheckCircle2 : SEV_ICON[f.severity];
  return (
    <li className={`rounded-lg border border-[var(--border)] bg-[var(--panel)] p-3 ${f.acknowledged ? "opacity-70" : ""}`}>
      <div className="flex items-start gap-2.5">
        <Icon className={`mt-0.5 size-4 shrink-0 ${f.acknowledged ? "text-[var(--ok)]" : SEV_COLOR[f.severity]}`} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{f.title}</span>
            <Badge>{f.rule_id}</Badge>
            <span className="text-xs text-[var(--muted)]">{f.category}</span>
          </div>
          <p className="mt-1 text-sm">{f.message}</p>
          <p className="mt-1 text-sm text-[var(--muted)]">
            <span className="font-medium text-[var(--text)]">Remediation: </span>
            {f.remediation}
          </p>
          <Evidence e={f.evidence} />
          {f.acknowledgement && (
            <p className="mt-2 text-xs text-[var(--ok)]">
              Acknowledged by {f.acknowledgement.by ?? "?"} · {relativeTime(f.acknowledgement.at)}: “{f.acknowledgement.reason}”
            </p>
          )}
        </div>
        {canAck && f.severity === "warning" && (
          <Button variant="ghost" onClick={f.acknowledged ? onRevoke : onAck} title={vm.native_id}>
            {f.acknowledged ? "Revoke" : "Acknowledge"}
          </Button>
        )}
      </div>
    </li>
  );
}

function RunForm({ ids }: { ids: string[] }) {
  const api = useApi();
  const navigate = useNavigate();
  const { workspaceId } = useWorkspace();
  const catalog = useQuery({ queryKey: ["catalog-status"], queryFn: api.catalogStatus });
  const [region, setRegion] = useState("");
  const [strategy, setStrategy] = useState("like_for_like");
  const regions = catalog.data?.azure ?? [];
  const effective = region || regions.find((r) => r.region === "westeurope")?.region || regions[0]?.region || "";
  const run = useMutation({
    mutationFn: () => api.assess(workspaceId, { resource_ids: ids, target_region: effective, strategy }),
    onSuccess: (r) => navigate(`/w/${workspaceId}/assessment/${r.id}`),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    run.mutate();
  };
  return (
    <Card title={`Assess ${ids.length} machine${ids.length === 1 ? "" : "s"} for Azure`} className="mb-6">
      <ErrorBanner error={run.error ? errorMessage(run.error) : null} />
      <form onSubmit={submit} className="grid items-end gap-3 sm:grid-cols-[1fr_1fr_auto]">
        <Field label="Target region">
          <Select value={effective} onChange={(e) => setRegion(e.target.value)} required>
            {regions.map((r) => (
              <option key={r.region}>{r.region}</option>
            ))}
          </Select>
        </Field>
        <Field label="Sizing strategy">
          <Select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="like_for_like">Like for like</option>
            <option value="cheapest_fit">Cheapest fit</option>
            <option value="right_sized">Right-sized</option>
          </Select>
        </Field>
        <Button type="submit" variant="primary" busy={run.isPending} disabled={!effective}>
          <ClipboardCheck className="size-4" /> Run assessment
        </Button>
      </form>
    </Card>
  );
}

function RunView({ runId }: { runId: string }) {
  const api = useApi();
  const qc = useQueryClient();
  const { workspaceId, can } = useWorkspace();
  const q = useQuery({ queryKey: ["assessment", workspaceId, runId], queryFn: () => api.assessment(workspaceId, runId) });
  const [open, setOpen] = useState<string | null>(null);
  const [filter, setFilter] = useState<ReadinessState | "">("");
  const [ackFor, setAckFor] = useState<{ vm: AssessedVm; f: Finding } | null>(null);
  const [reason, setReason] = useState("");
  const [info, setInfo] = useState<string | null>(null);

  const ack = useMutation({
    mutationFn: () => api.acknowledge(workspaceId, { native_id: ackFor!.vm.native_id, rule_id: ackFor!.f.rule_id, reason }),
    onSuccess: () => {
      setAckFor(null);
      setReason("");
      setInfo("Acknowledged. It applies to the next assessment run of this machine.");
    },
  });
  const revoke = useMutation({
    mutationFn: (x: { vm: AssessedVm; f: Finding }) => api.revokeAck(workspaceId, x.vm.native_id, x.f.rule_id),
    onSuccess: () => setInfo("Acknowledgement revoked. It applies to the next assessment run."),
  });

  if (q.isLoading) return <Spinner />;
  if (q.error || !q.data) return <ErrorBanner error={q.error ? errorMessage(q.error) : "Not found"} />;
  const run = q.data;
  const items = (run.items ?? []).filter((i) => !filter || i.readiness === filter);

  return (
    <>
      <div className="-mt-2 mb-4 flex justify-end">
        <Link className="text-sm text-[var(--accent)]" to={`/w/${workspaceId}/plans`}>
          Create a migration plan from this assessment →
        </Link>
      </div>
      <ErrorBanner error={ack.error ? errorMessage(ack.error) : revoke.error ? errorMessage(revoke.error) : null} />
      {info && (
        <div className="mb-4 flex items-center justify-between rounded-md border border-[var(--border)] bg-[var(--accent-soft)] px-3 py-2 text-sm">
          {info}
          <Button variant="ghost" onClick={() => void qc.invalidateQueries({ queryKey: ["assessments", workspaceId] })}>
            OK
          </Button>
        </div>
      )}
      <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {(["ready", "ready_with_changes", "blocked"] as ReadinessState[]).map((r) => (
          <button
            key={r}
            onClick={() => setFilter(filter === r ? "" : r)}
            className={`rounded-xl border bg-[var(--panel)] p-4 text-left shadow-[var(--shadow)] ${filter === r ? "border-[var(--accent)]" : "border-[var(--border)]"}`}
          >
            <div className="text-xs font-medium text-[var(--muted)]">{READINESS[r].label}</div>
            <div className="mt-2 text-2xl font-semibold tabular-nums">{run.summary.readiness[r] ?? 0}</div>
          </button>
        ))}
        <div className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-4 shadow-[var(--shadow)]">
          <div className="text-xs font-medium text-[var(--muted)]">Average readiness score</div>
          <div className="mt-2 text-2xl font-semibold tabular-nums">{run.summary.average_score}</div>
        </div>
      </div>

      <div className="mb-6 grid gap-6 lg:grid-cols-2">
        <Card title="Most common open issues">
          {run.summary.top_issues.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">No open warnings or blockers.</p>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {run.summary.top_issues.map(([rule, n]) => (
                <li key={rule} className="flex justify-between">
                  <Badge>{rule}</Badge>
                  <span className="tabular-nums">
                    {n} machine{n === 1 ? "" : "s"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title={`vCPU quota needed in ${run.target_region}`}>
          <p className="mb-2 text-xs text-[var(--muted)]">Azure quotas are per VM family and region. Request increases before migration waves.</p>
          <ul className="space-y-1.5 text-sm">
            {run.summary.quota_needs.map((q) => (
              <li key={q.family} className="flex justify-between">
                <span>{q.family}</span>
                <span className="tabular-nums">{q.vcpu} vCPU</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title={`Machines (${items.length})`}>
        <Table head={["", "Machine", "Readiness", "Score", "Target", "Blockers", "Warnings"]}>
          {items.map((vm) => {
            const isOpen = open === vm.resource_id;
            const count = (s: Severity) => vm.findings.filter((f) => f.severity === s && !f.acknowledged).length;
            return (
              <Fragment key={vm.resource_id}>
                <tr className="cursor-pointer border-b border-[var(--border)] hover:bg-[var(--panel-2)]" onClick={() => setOpen(isOpen ? null : vm.resource_id)}>
                  <td className="w-6 px-2 py-2 text-[var(--muted)]">{isOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</td>
                  <td className="px-3 py-2">
                    <div className="font-medium">{vm.name ?? vm.native_id}</div>
                    <div className="font-mono text-xs text-[var(--muted)]">{vm.native_id}</div>
                  </td>
                  <td className="px-3 py-2">
                    <ReadinessBadge r={vm.readiness} />
                  </td>
                  <td className="px-3 py-2 tabular-nums">{vm.score}</td>
                  <td className="px-3 py-2 font-mono text-xs">{vm.target_sku ?? "—"}</td>
                  <td className="px-3 py-2 tabular-nums">{count("blocker") || "—"}</td>
                  <td className="px-3 py-2 tabular-nums">{count("warning") || "—"}</td>
                </tr>
                {isOpen && (
                  <tr className="border-b border-[var(--border)] bg-[var(--panel-2)]">
                    <td colSpan={7} className="px-4 py-4">
                      <ul className="space-y-2">
                        {vm.findings.map((f) => (
                          <FindingRow
                            key={f.rule_id + f.title}
                            f={f}
                            vm={vm}
                            canAck={can("analyst")}
                            onAck={() => setAckFor({ vm, f })}
                            onRevoke={() => revoke.mutate({ vm, f })}
                          />
                        ))}
                      </ul>
                      <Link to={`/w/${workspaceId}/inventory/${vm.resource_id}`} className="mt-3 inline-block text-sm text-[var(--accent)]">
                        Open machine →
                      </Link>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </Table>
      </Card>

      <Dialog open={ackFor !== null} onClose={() => setAckFor(null)} title={`Acknowledge ${ackFor?.f.rule_id ?? ""}`}>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            ack.mutate();
          }}
        >
          <p className="text-sm text-[var(--muted)]">
            {ackFor?.f.title} on <span className="font-mono">{ackFor?.vm.name ?? ackFor?.vm.native_id}</span>. Acknowledgements are recorded in the audit log and
            persist across discovery runs. Blockers cannot be acknowledged.
          </p>
          <Field label="Reason">
            <Input required minLength={5} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Hyper-V modules verified in initramfs" />
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" onClick={() => setAckFor(null)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" busy={ack.isPending}>
              Acknowledge
            </Button>
          </div>
        </form>
      </Dialog>
    </>
  );
}

export function Assessment() {
  const api = useApi();
  const { workspaceId } = useWorkspace();
  const { runId } = useParams();
  const [params] = useSearchParams();
  const ids = (params.get("ids") ?? "").split(",").filter(Boolean);
  const runs = useQuery({ queryKey: ["assessments", workspaceId], queryFn: () => api.assessments(workspaceId), enabled: !runId });
  const current = useQuery({ queryKey: ["assessment", workspaceId, runId], queryFn: () => api.assessment(workspaceId, runId!), enabled: !!runId });

  return (
    <>
      <PageHeader
        title="Migration readiness"
        subtitle={
          runId && current.data
            ? `Azure ${current.data.target_region} · ${current.data.strategy.replace(/_/g, " ")} · rules ${current.data.ruleset_version} · ${relativeTime(current.data.created_at)}`
            : "Deterministic rules check each machine for blockers and required changes before migration."
        }
      />
      {runId ? (
        <RunView runId={runId} />
      ) : (
        <>
          {ids.length > 0 && <RunForm ids={ids} />}
          <Card title="Assessment runs">
            {runs.isLoading ? (
              <Spinner />
            ) : (runs.data ?? []).length === 0 ? (
              <EmptyState icon={<ClipboardCheck className="size-8" />} title="No assessments yet">
                Select machines in the <Link className="text-[var(--accent)]" to={`/w/${workspaceId}/inventory`}>inventory</Link> and choose “Assess”.
              </EmptyState>
            ) : (
              <Table head={["When", "Target", "Machines", "Ready", "With changes", "Blocked", "Avg score"]}>
                {runs.data!.map((r) => (
                  <tr key={r.id} className="border-b border-[var(--border)] hover:bg-[var(--panel-2)]">
                    <td className="px-3 py-2">
                      <Link className="text-[var(--accent)]" to={r.id}>
                        {relativeTime(r.created_at)}
                      </Link>
                    </td>
                    <td className="px-3 py-2">Azure {r.target_region}</td>
                    <td className="px-3 py-2 tabular-nums">{r.summary.vms}</td>
                    <td className="px-3 py-2 tabular-nums">{r.summary.readiness.ready ?? 0}</td>
                    <td className="px-3 py-2 tabular-nums">{r.summary.readiness.ready_with_changes ?? 0}</td>
                    <td className="px-3 py-2 tabular-nums">{r.summary.readiness.blocked ?? 0}</td>
                    <td className="px-3 py-2 tabular-nums">{r.summary.average_score}</td>
                  </tr>
                ))}
              </Table>
            )}
          </Card>
        </>
      )}
    </>
  );
}
