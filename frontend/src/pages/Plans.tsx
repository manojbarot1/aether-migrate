import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileCode2, FileText, GitBranch, ListChecks, Map as MapIcon, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Badge, Button, Card, Code, Dialog, EmptyState, ErrorBanner, Field, Input, PageHeader, Select, Spinner, Table, relativeTime } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useApi, useWorkspace } from "../lib/context";
import type { PlanDetail, PlanSummary } from "../lib/types";

const STATUS_TONE: Record<PlanSummary["status"], "neutral" | "ok" | "warn" | "err" | "accent"> = {
  draft: "neutral",
  in_review: "accent",
  approved: "ok",
  rejected: "err",
  superseded: "neutral",
};

function StatusPill({ s }: { s: PlanSummary["status"] }) {
  return <Badge tone={STATUS_TONE[s]}>{s.replace("_", " ")}</Badge>;
}

const usd = (v: number | null | undefined) =>
  v == null ? "—" : new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v);

function CreatePlan() {
  const api = useApi();
  const navigate = useNavigate();
  const { workspaceId } = useWorkspace();
  const runs = useQuery({ queryKey: ["assessments", workspaceId], queryFn: () => api.assessments(workspaceId) });
  const [name, setName] = useState("");
  const [runId, setRunId] = useState("");
  const [mechanism, setMechanism] = useState("azure_migrate");
  const [bandwidth, setBandwidth] = useState("500");
  const [windowText, setWindowText] = useState("Saturday 22:00-02:00 UTC");
  const effective = runId || runs.data?.[0]?.id || "";
  const create = useMutation({
    mutationFn: () =>
      api.createPlan(workspaceId, {
        name,
        assessment_run_id: effective,
        options: { mechanism, replication_bandwidth_mbps: Number(bandwidth), cutover_window: windowText },
      }),
    onSuccess: (p) => navigate(`/w/${workspaceId}/plans/${p.id}`),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  if (runs.data && runs.data.length === 0)
    return (
      <Card className="mb-6">
        <p className="text-sm text-[var(--muted)]">
          Plans are built from a readiness assessment. <Link className="text-[var(--accent)]" to={`/w/${workspaceId}/inventory`}>Select machines</Link> and run an assessment first.
        </p>
      </Card>
    );
  return (
    <Card title="New plan" className="mb-6">
      <ErrorBanner error={create.error ? errorMessage(create.error) : null} />
      <form onSubmit={submit} className="grid items-end gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Field label="Name">
          <Input required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} placeholder="Wave 1 – web tier" />
        </Field>
        <Field label="From assessment">
          <Select value={effective} onChange={(e) => setRunId(e.target.value)} required>
            {(runs.data ?? []).map((r) => (
              <option key={r.id} value={r.id}>
                {relativeTime(r.created_at)} · {r.summary.vms} VMs · {r.target_region}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Mechanism">
          <Select value={mechanism} onChange={(e) => setMechanism(e.target.value)}>
            <option value="azure_migrate">Azure Migrate replication</option>
            <option value="cold_image">Cold image export/import</option>
          </Select>
        </Field>
        <Field label="Bandwidth (Mbps)">
          <Input type="number" min={10} value={bandwidth} onChange={(e) => setBandwidth(e.target.value)} />
        </Field>
        <Field label="Cutover window">
          <Input value={windowText} onChange={(e) => setWindowText(e.target.value)} />
        </Field>
        <Button type="submit" variant="primary" busy={create.isPending} disabled={!effective}>
          Generate plan
        </Button>
      </form>
    </Card>
  );
}

function PlanList() {
  const api = useApi();
  const { workspaceId, can } = useWorkspace();
  const plans = useQuery({ queryKey: ["plans", workspaceId], queryFn: () => api.plans(workspaceId) });
  return (
    <>
      <PageHeader title="Migration plans" subtitle="Deterministic, versioned plans with a content hash that approvals sign. Content never changes after creation." />
      {can("analyst") && <CreatePlan />}
      <ErrorBanner error={plans.error ? errorMessage(plans.error) : null} />
      <Card>
        {plans.isLoading ? (
          <Spinner />
        ) : (plans.data ?? []).length === 0 ? (
          <EmptyState icon={<MapIcon className="size-8" />} title="No plans yet" />
        ) : (
          <Table head={["Plan", "Status", "Machines", "Waves", "Target / month", "Author", "Created"]}>
            {plans.data!.map((p) => (
              <tr key={p.id} className="border-b border-[var(--border)] hover:bg-[var(--panel-2)]">
                <td className="px-3 py-2">
                  <Link className="font-medium hover:text-[var(--accent)]" to={p.id}>
                    {p.name}
                  </Link>
                  <span className="ml-2 text-xs text-[var(--muted)]">v{p.version}</span>
                </td>
                <td className="px-3 py-2">
                  <StatusPill s={p.status} />
                </td>
                <td className="px-3 py-2 tabular-nums">{p.totals.vms}</td>
                <td className="px-3 py-2 tabular-nums">{p.totals.waves}</td>
                <td className="px-3 py-2 tabular-nums">{usd(p.totals.target_monthly_usd?.on_demand)}</td>
                <td className="px-3 py-2 text-[var(--muted)]">{p.created_by_display ?? "—"}</td>
                <td className="px-3 py-2 text-[var(--muted)]">{relativeTime(p.created_at)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}

type Tab = "overview" | "waves" | "scope" | "iac" | "review";

function IacViewer({ plan }: { plan: PlanDetail }) {
  const api = useApi();
  const { workspaceId } = useWorkspace();
  const [file, setFile] = useState(plan.iac_files.includes("main.tf") ? "main.tf" : (plan.iac_files[0] ?? ""));
  const q = useQuery({ queryKey: ["iac", plan.id, file], queryFn: () => api.planIac(workspaceId, plan.id, file), enabled: !!file, staleTime: Infinity });
  return (
    <Card
      title="Landing zone (OpenTofu, azurerm 4.x)"
      actions={
        <Button onClick={() => void api.downloadPlan(workspaceId, plan.id, "opentofu")}>
          <Download className="size-4" /> Download module
        </Button>
      }
    >
      {plan.iac_notes.length > 0 && (
        <div className="mb-3 rounded-md border border-[var(--border)] bg-[var(--panel-2)] p-3 text-sm">
          <div className="mb-1 font-medium">Translation notes</div>
          <ul className="list-disc space-y-1 pl-5 text-[var(--muted)]">
            {plan.iac_notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="mb-2 flex flex-wrap gap-1">
        {plan.iac_files.map((f) => (
          <button
            key={f}
            onClick={() => setFile(f)}
            className={`rounded-md px-2.5 py-1 font-mono text-xs ${f === file ? "bg-[var(--accent-soft)] font-medium" : "text-[var(--muted)] hover:bg-[var(--panel-2)]"}`}
          >
            {f}
          </button>
        ))}
      </div>
      {q.isLoading ? <Spinner /> : q.error ? <ErrorBanner error={errorMessage(q.error)} /> : <Code>{q.data ?? ""}</Code>}
    </Card>
  );
}

function PlanView({ planId }: { planId: string }) {
  const api = useApi();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { workspaceId, can } = useWorkspace();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const q = useQuery({ queryKey: ["plan", workspaceId, planId], queryFn: () => api.plan(workspaceId, planId) });
  const [tab, setTab] = useState<Tab>("overview");
  const [review, setReview] = useState<"approve" | "reject" | null>(null);
  const [comment, setComment] = useState("");
  const refresh = (p: PlanDetail) => {
    qc.setQueryData(["plan", workspaceId, p.id], p);
    void qc.invalidateQueries({ queryKey: ["plans", workspaceId] });
  };
  const submit = useMutation({ mutationFn: () => api.submitPlan(workspaceId, planId), onSuccess: refresh });
  const decide = useMutation({
    mutationFn: () => api.reviewPlan(workspaceId, planId, { decision: review, comment, content_hash: q.data!.content_hash }),
    onSuccess: (p) => {
      setReview(null);
      setComment("");
      refresh(p);
    },
  });
  const revise = useMutation({
    mutationFn: () => api.revisePlan(workspaceId, planId, {}),
    onSuccess: (p) => navigate(`/w/${workspaceId}/plans/${p.id}`),
  });

  if (q.isLoading) return <Spinner />;
  if (q.error || !q.data) return <ErrorBanner error={q.error ? errorMessage(q.error) : "Not found"} />;
  const p = q.data;
  const c = p.content;
  const isAuthor = p.created_by_display != null && p.created_by_display === (me.data?.email ?? me.data?.display_name);
  const latest = Math.max(...p.versions.map((v) => v.version));
  const err = submit.error ?? decide.error ?? revise.error;

  const tabs: [Tab, string, typeof ListChecks][] = [
    ["overview", "Overview", ListChecks],
    ["waves", `Waves (${c.waves.length})`, GitBranch],
    ["scope", `Scope (${c.scope.length})`, MapIcon],
    ["iac", "Landing zone", FileCode2],
    ["review", `Review (${p.reviews.length})`, ShieldCheck],
  ];

  return (
    <>
      <PageHeader
        title={`${p.name}`}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <StatusPill s={p.status} /> v{p.version} · Azure {c.target.region} · {c.options.mechanism.replace("_", " ")} · by {p.created_by_display ?? "?"} ·{" "}
            {relativeTime(p.created_at)}
          </span>
        }
        actions={
          <>
            <Button onClick={() => void api.downloadPlan(workspaceId, p.id, "markdown")}>
              <FileText className="size-4" /> Markdown
            </Button>
            <Button onClick={() => void api.downloadPlan(workspaceId, p.id, "json")}>
              <Download className="size-4" /> JSON
            </Button>
            {p.status === "draft" && can("analyst") && (
              <Button variant="primary" busy={submit.isPending} disabled={p.totals.open_blockers.length > 0} onClick={() => submit.mutate()}>
                Submit for review
              </Button>
            )}
            {p.status === "in_review" && can("approver") && !isAuthor && (
              <>
                <Button variant="danger" onClick={() => setReview("reject")}>
                  Reject
                </Button>
                <Button variant="primary" onClick={() => setReview("approve")}>
                  Approve
                </Button>
              </>
            )}
            {p.version === latest && p.status !== "superseded" && can("analyst") && (
              <Button busy={revise.isPending} onClick={() => revise.mutate()}>
                New version
              </Button>
            )}
          </>
        }
      />
      <ErrorBanner error={err ? errorMessage(err) : null} />
      <div className="mb-4 font-mono text-[11px] text-[var(--muted)]" title="SHA-256 of the canonical plan content; approvals are bound to it">
        content hash {p.content_hash}
      </div>
      {p.totals.open_blockers.length > 0 && (
        <ErrorBanner error={`Unresolved blockers prevent submission: ${p.totals.open_blockers.join(", ")}`} />
      )}

      <div className="mb-4 flex gap-1 overflow-x-auto border-b border-[var(--border)]">
        {tabs.map(([key, label, Icon]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm whitespace-nowrap ${tab === key ? "border-[var(--accent)] font-medium" : "border-transparent text-[var(--muted)] hover:text-[var(--text)]"}`}
          >
            <Icon className="size-4" /> {label}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="grid gap-4 sm:grid-cols-2 lg:col-span-3 xl:grid-cols-4">
            {(
              [
                ["Machines", `${p.totals.vms}`, `${p.totals.data_gib} GiB`],
                ["Waves", `${p.totals.waves}`, c.options.cutover_window],
                ["Target / month", usd(p.totals.target_monthly_usd.on_demand), `3-yr reserved ${usd(p.totals.target_monthly_usd.reserved_3y)}`],
                ["One-time", usd(p.totals.one_time_usd), `${p.totals.open_warnings} open warnings`],
              ] as const
            ).map(([label, value, hint]) => (
              <div key={label} className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-4 shadow-[var(--shadow)]">
                <div className="text-xs font-medium text-[var(--muted)]">{label}</div>
                <div className="mt-2 text-2xl font-semibold tabular-nums">{value}</div>
                <div className="mt-0.5 text-xs text-[var(--muted)]">{hint}</div>
              </div>
            ))}
          </div>
          <Card title="Prerequisites" className="lg:col-span-2">
            <ol className="space-y-3 text-sm">
              {c.prerequisites.map((x) => (
                <li key={x.id}>
                  <div className="font-medium">
                    {x.id} · {x.title}
                  </div>
                  <p className="text-[var(--muted)]">{x.detail}</p>
                  {Object.keys(x.evidence).length > 0 && (
                    <pre className="mt-1 overflow-x-auto rounded bg-[var(--panel-2)] p-2 font-mono text-[11px]">{JSON.stringify(x.evidence, null, 2)}</pre>
                  )}
                </li>
              ))}
            </ol>
          </Card>
          <div className="space-y-6">
            <Card title="Rollback">
              <ol className="list-decimal space-y-1 pl-5 text-sm">
                {c.rollback.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ol>
            </Card>
            <Card title="Assumptions">
              <ul className="list-disc space-y-1 pl-5 text-sm text-[var(--muted)]">
                {c.assumptions.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            </Card>
            {c.excluded.length > 0 && (
              <Card title={`Excluded (${c.excluded.length})`}>
                <ul className="space-y-1 text-sm">
                  {c.excluded.map((e) => (
                    <li key={e.native_id}>
                      <span className="font-medium">{e.name ?? e.native_id}</span> <span className="text-[var(--muted)]">— {e.reason}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>
        </div>
      )}

      {tab === "waves" && (
        <div className="space-y-6">
          {c.waves.map((w) => (
            <Card key={w.number} title={w.name} actions={<span className="text-xs text-[var(--muted)]">{w.vms.length} machines</span>}>
              <p className="mb-3 text-sm text-[var(--muted)]">
                {w.reason}. {w.data_gib} GiB · initial sync ≈ {w.initial_sync_hours} h · cutover downtime ≈ {w.cutover_downtime_minutes} min
              </p>
              <p className="mb-3 font-mono text-xs">{w.vms.join(", ")}</p>
              <Table head={["Step", "Pre-check", "Action", "Post-check", "Compensation"]}>
                {w.steps.map((s) => (
                  <tr key={s.id} className="border-b border-[var(--border)] align-top">
                    <td className="px-3 py-2 font-medium whitespace-nowrap">
                      {s.id} {s.title}
                    </td>
                    <td className="px-3 py-2 text-[var(--muted)]">{s.pre_check}</td>
                    <td className="px-3 py-2">{s.action}</td>
                    <td className="px-3 py-2 text-[var(--muted)]">{s.post_check}</td>
                    <td className="px-3 py-2 text-[var(--muted)]">{s.compensation}</td>
                  </tr>
                ))}
              </Table>
            </Card>
          ))}
        </div>
      )}

      {tab === "scope" && (
        <Card>
          <Table head={["Machine", "OS", "Source", "Target", "Disks", "Readiness", "Open findings", "Target / mo"]}>
            {c.scope.map((v) => (
              <tr key={v.native_id} className="border-b border-[var(--border)]">
                <td className="px-3 py-2">
                  <Link className="font-medium hover:text-[var(--accent)]" to={`/w/${workspaceId}/inventory/${v.resource_id}`}>
                    {v.name ?? v.native_id}
                  </Link>
                </td>
                <td className="px-3 py-2 text-[var(--muted)]">{v.os ?? "unknown"}</td>
                <td className="px-3 py-2 font-mono text-xs">{v.source_sku}</td>
                <td className="px-3 py-2 font-mono text-xs">{v.target_sku ?? "—"}</td>
                <td className="px-3 py-2 tabular-nums">{v.disks_gib} GiB</td>
                <td className="px-3 py-2">{v.readiness.replace(/_/g, " ")}</td>
                <td className="px-3 py-2">
                  <span className="flex flex-wrap gap-1">
                    {v.open_findings.map((f) => (
                      <Badge key={f.rule_id} tone={f.severity === "blocker" ? "err" : "warn"}>
                        {f.rule_id}
                      </Badge>
                    ))}
                  </span>
                </td>
                <td className="px-3 py-2 tabular-nums">{usd(v.monthly_usd.on_demand)}</td>
              </tr>
            ))}
          </Table>
        </Card>
      )}

      {tab === "iac" && <IacViewer plan={p} />}

      {tab === "review" && (
        <div className="grid gap-6 lg:grid-cols-3">
          <Card title="Decisions" className="lg:col-span-2">
            {p.reviews.length === 0 ? (
              <p className="text-sm text-[var(--muted)]">
                {p.status === "draft" ? "Submit the plan to request a review." : "No decision yet. An approver other than the author must review it."}
              </p>
            ) : (
              <ul className="space-y-3 text-sm">
                {p.reviews.map((r, i) => (
                  <li key={i} className="rounded-lg border border-[var(--border)] p-3">
                    <div className="flex items-center gap-2">
                      <Badge tone={r.decision === "approve" ? "ok" : "err"}>{r.decision}d</Badge>
                      <span className="font-medium">{r.reviewer_display}</span>
                      <span className="text-xs text-[var(--muted)]">{relativeTime(r.created_at)}</span>
                    </div>
                    <p className="mt-1">{r.comment}</p>
                    <p className="mt-1 font-mono text-[11px] text-[var(--muted)]">
                      signed hash {r.content_hash.slice(0, 16)}… {r.expires_at && `· valid until ${new Date(r.expires_at).toLocaleDateString()}`}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Versions">
            <ul className="space-y-1.5 text-sm">
              {p.versions.map((v) => (
                <li key={v.id} className="flex items-center justify-between">
                  <Link className={v.id === p.id ? "font-medium" : "text-[var(--accent)]"} to={`/w/${workspaceId}/plans/${v.id}`}>
                    v{v.version}
                  </Link>
                  <span className="flex items-center gap-2 text-xs text-[var(--muted)]">
                    {relativeTime(v.created_at)} <StatusPill s={v.status as PlanSummary["status"]} />
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}

      <Dialog open={review !== null} onClose={() => setReview(null)} title={review === "approve" ? "Approve plan" : "Reject plan"}>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            decide.mutate();
          }}
        >
          <p className="text-sm text-[var(--muted)]">
            Your decision is recorded against content hash <span className="font-mono">{p.content_hash.slice(0, 16)}…</span>. If the plan changes, a new
            version needs a new review.
          </p>
          <ErrorBanner error={decide.error ? errorMessage(decide.error) : null} />
          <Field label="Comment">
            <Input required minLength={3} value={comment} onChange={(e) => setComment(e.target.value)} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" onClick={() => setReview(null)}>
              Cancel
            </Button>
            <Button type="submit" variant={review === "reject" ? "danger" : "primary"} busy={decide.isPending}>
              {review === "approve" ? "Approve" : "Reject"}
            </Button>
          </div>
        </form>
      </Dialog>
    </>
  );
}

export function Plans() {
  const { planId } = useParams();
  return planId ? <PlanView planId={planId} /> : <PlanList />;
}
