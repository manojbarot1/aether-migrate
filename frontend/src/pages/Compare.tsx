import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Calculator, ChevronDown, ChevronRight, RefreshCw, TriangleAlert } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Badge, Button, Card, EmptyState, ErrorBanner, Field, Input, PageHeader, Select, Spinner, Table, relativeTime } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useApi, useWorkspace } from "../lib/context";
import { gib } from "../lib/format";
import type { CompareResult, Money, PriceScenario } from "../lib/types";

const SCENARIOS: { key: PriceScenario; label: string }[] = [
  { key: "on_demand", label: "Pay as you go" },
  { key: "reserved_1y", label: "1-year reserved" },
  { key: "reserved_3y", label: "3-year reserved" },
];
const CURRENCIES = ["EUR", "USD", "GBP", "PLN", "CHF", "SEK", "NOK", "DKK", "CZK", "JPY", "INR"];

function useMoney(result: CompareResult | undefined) {
  return useMemo(() => {
    const fmt = new Intl.NumberFormat(undefined, { style: "currency", currency: result?.currency ?? "USD", maximumFractionDigits: 0 });
    const rate = result?.fx_per_usd ?? 1;
    return (usd: number | null | undefined) => (usd == null ? "—" : fmt.format(usd * rate));
  }, [result]);
}

function delta(src: number | null | undefined, tgt: number | null | undefined) {
  if (src == null || tgt == null || src === 0) return null;
  return (tgt - src) / src;
}

function DeltaBadge({ d }: { d: number | null }) {
  if (d === null) return null;
  const pct = `${d > 0 ? "+" : ""}${(d * 100).toFixed(0)}%`;
  return <Badge tone={d <= 0 ? "ok" : "warn"}>{pct}</Badge>;
}

export function Compare() {
  const api = useApi();
  const qc = useQueryClient();
  const { workspaceId, workspace } = useWorkspace();
  const [params] = useSearchParams();
  const ids = (params.get("ids") ?? "").split(",").filter(Boolean);
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });

  const catalog = useQuery({ queryKey: ["catalog-status"], queryFn: api.catalogStatus });
  const regions = catalog.data?.azure ?? [];
  const [region, setRegion] = useState("");
  const [strategy, setStrategy] = useState("like_for_like");
  const [currency, setCurrency] = useState(String(workspace?.settings?.currency ?? "EUR"));
  const [diskClass, setDiskClass] = useState("premium_ssd");
  const [egress, setEgress] = useState("0.09");
  const [dual, setDual] = useState("14");
  const [scenario, setScenario] = useState<PriceScenario>("on_demand");
  const [open, setOpen] = useState<string | null>(null);
  const effectiveRegion = region || regions.find((r) => r.region === "westeurope")?.region || regions[0]?.region || "";

  const run = useMutation({
    mutationFn: () =>
      api.compare(workspaceId, {
        resource_ids: ids,
        target_region: effectiveRegion,
        strategy,
        options: { currency, target_disk_class: diskClass, egress_usd_per_gib: Number(egress), dual_running_days: Number(dual) },
      }),
  });
  const sync = useMutation({
    mutationFn: api.catalogSync,
    onSuccess: () => setTimeout(() => void qc.invalidateQueries({ queryKey: ["catalog-status"] }), 15000),
  });
  const r = run.data;
  const money = useMoney(r);

  if (ids.length === 0)
    return (
      <>
        <PageHeader title="Compare target costs" />
        <Card>
          <EmptyState icon={<Calculator className="size-8" />} title="Select machines to compare">
            Pick VMs in the <Link className="text-[var(--accent)]" to={`/w/${workspaceId}/inventory`}>inventory</Link>, then choose “Compare to Azure”.
          </EmptyState>
        </Card>
      </>
    );

  const tot = r?.totals;
  const srcTotal = tot?.source_monthly_usd[scenario];
  const tgtTotal = tot?.target_monthly_usd[scenario];

  return (
    <>
      <PageHeader
        title="Compare target costs"
        subtitle={`${ids.length} machine${ids.length === 1 ? "" : "s"} · AWS → Azure · deterministic sizing and list-price estimates (not a quote)`}
      />
      <ErrorBanner error={run.error ? errorMessage(run.error) : catalog.error ? errorMessage(catalog.error) : null} />

      {catalog.data && regions.length === 0 && (
        <Card className="mb-4">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <TriangleAlert className="size-4 text-[var(--warn)]" />
            <span className="flex-1">The Azure price catalog is empty. It syncs daily; a platform administrator can start a sync now.</span>
            {me.data?.is_platform_admin && (
              <Button busy={sync.isPending} onClick={() => sync.mutate()}>
                <RefreshCw className="size-4" /> Sync catalog
              </Button>
            )}
          </div>
          {sync.isSuccess && <p className="mt-2 text-xs text-[var(--muted)]">Sync started; prices appear within a minute.</p>}
        </Card>
      )}

      <Card className="mb-6">
        <form
          className="grid gap-3 sm:grid-cols-3 lg:grid-cols-7"
          onSubmit={(e) => {
            e.preventDefault();
            run.mutate();
          }}
        >
          <Field label="Target region">
            <Select value={effectiveRegion} onChange={(e) => setRegion(e.target.value)} required>
              {regions.map((x) => (
                <option key={x.region} value={x.region}>
                  {x.region}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Sizing">
            <Select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              <option value="like_for_like">Like for like</option>
              <option value="cheapest_fit">Cheapest fit</option>
              <option value="right_sized">Right-sized (metrics)</option>
            </Select>
          </Field>
          <Field label="Currency">
            <Select value={currency} onChange={(e) => setCurrency(e.target.value)}>
              {CURRENCIES.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </Select>
          </Field>
          <Field label="Disks">
            <Select value={diskClass} onChange={(e) => setDiskClass(e.target.value)}>
              <option value="premium_ssd">Premium SSD</option>
              <option value="standard_ssd">Standard SSD</option>
            </Select>
          </Field>
          <Field label="Egress $/GiB">
            <Input type="number" min={0} step="0.01" value={egress} onChange={(e) => setEgress(e.target.value)} />
          </Field>
          <Field label="Dual-run days">
            <Input type="number" min={0} max={365} value={dual} onChange={(e) => setDual(e.target.value)} />
          </Field>
          <div className="flex items-end">
            <Button type="submit" variant="primary" className="w-full" busy={run.isPending} disabled={!effectiveRegion}>
              <Calculator className="size-4" /> Compare
            </Button>
          </div>
        </form>
      </Card>

      {run.isPending && <Spinner label="Sizing and pricing" />}
      {r && tot && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            {SCENARIOS.map((s) => (
              <button
                key={s.key}
                onClick={() => setScenario(s.key)}
                className={`rounded-full border px-3 py-1 text-sm ${scenario === s.key ? "border-[var(--accent)] bg-[var(--accent-soft)] font-medium" : "border-[var(--border)] text-[var(--muted)]"}`}
              >
                {s.label}
              </button>
            ))}
            <span className="ml-auto text-xs text-[var(--muted)]">
              Azure prices {r.target_prices_as_of ? `as of ${relativeTime(r.target_prices_as_of)}` : "—"}
              {r.fx_date && ` · FX ${r.currency}/USD ${r.fx_per_usd.toFixed(4)} (ECB ${r.fx_date})`}
            </span>
          </div>
          {r.target_price_stale && <ErrorBanner error="Target prices are more than 48 hours old; results may be out of date." />}
          {!r.source_prices_available && (
            <div className="mb-4 rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-2 text-sm text-[var(--muted)]">
              Source (AWS) list prices are unavailable for these machines: the connection lacks <code>pricing:GetProducts</code> or the Price List API was
              unreachable. Target costs are shown; savings cannot be computed.
            </div>
          )}

          <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {[
              ["Current (AWS) per month", money(srcTotal), null],
              ["Target (Azure) per month", money(tgtTotal), <DeltaBadge key="d" d={delta(srcTotal, tgtTotal)} />],
              ["Target per year", money(tgtTotal == null ? null : tgtTotal * 12), null],
              ["One-time migration", money(tot.one_time_usd), <span key="h" className="text-xs text-[var(--muted)]">egress + dual running</span>],
            ].map(([label, value, extra], i) => (
              <div key={i} className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-4 shadow-[var(--shadow)]">
                <div className="text-xs font-medium text-[var(--muted)]">{label}</div>
                <div className="mt-2 flex items-center gap-2 text-2xl font-semibold tabular-nums">
                  {value} {extra}
                </div>
              </div>
            ))}
          </div>

          <Card title={`Machines (${r.items.length})${tot.unsized ? ` · ${tot.unsized} without a match` : ""}`}>
            <Table head={["", "Machine", "Source", "", "Recommended", "Source / mo", "Target / mo", "Δ"]}>
              {r.items.map((it) => {
                const c = it.sizing.candidates[0];
                const src = it.cost?.source.total_monthly_usd[scenario];
                const tgt = it.cost?.target.total_monthly_usd[scenario];
                const isOpen = open === it.resource_id;
                return (
                  <Fragment key={it.resource_id}>
                    <tr className="cursor-pointer border-b border-[var(--border)] hover:bg-[var(--panel-2)]" onClick={() => setOpen(isOpen ? null : it.resource_id)}>
                      <td className="w-6 px-2 py-2 text-[var(--muted)]">{isOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</td>
                      <td className="px-3 py-2">
                        <div className="font-medium">{it.name ?? it.native_id}</div>
                        <div className="text-xs text-[var(--muted)]">{it.region}</div>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">{it.source_sku ?? "—"}</td>
                      <td className="px-1 text-[var(--muted)]">
                        <ArrowRight className="size-4" />
                      </td>
                      <td className="px-3 py-2">
                        {c ? (
                          <>
                            <div className="font-mono text-xs">{c.sku}</div>
                            <div className="text-xs text-[var(--muted)]">
                              {c.vcpu} vCPU · {gib(c.memory_mib)}
                            </div>
                          </>
                        ) : (
                          <Badge tone="warn">no match</Badge>
                        )}
                      </td>
                      <td className="px-3 py-2 tabular-nums">{money(src)}</td>
                      <td className="px-3 py-2 font-medium tabular-nums">{money(tgt)}</td>
                      <td className="px-3 py-2">
                        <DeltaBadge d={delta(src, tgt)} />
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="border-b border-[var(--border)] bg-[var(--panel-2)]">
                        <td colSpan={8} className="px-4 py-4">
                          <div className="grid gap-4 text-sm lg:grid-cols-3">
                            <div>
                              <div className="mb-1 text-xs font-semibold tracking-wide text-[var(--muted)] uppercase">Why this size</div>
                              <ul className="list-disc space-y-1 pl-4">
                                {(c?.reasons ?? []).concat(it.sizing.requirement.notes, it.sizing.warnings).map((x, i) => (
                                  <li key={i}>{x}</li>
                                ))}
                              </ul>
                              {it.sizing.candidates.length > 1 && (
                                <p className="mt-2 text-xs text-[var(--muted)]">Alternatives: {it.sizing.candidates.slice(1).map((a) => a.sku).join(", ")}</p>
                              )}
                            </div>
                            <div>
                              <div className="mb-1 text-xs font-semibold tracking-wide text-[var(--muted)] uppercase">Monthly breakdown</div>
                              <CostLines label="Target compute" v={it.cost?.target.compute_monthly_usd} money={money} />
                              <div className="flex justify-between py-0.5">
                                <span>Target disks</span>
                                <span className="tabular-nums">{money(it.cost?.target.disks_monthly_usd)}</span>
                              </div>
                              {(it.cost?.target.disks ?? []).map((d, i) => (
                                <div key={i} className="flex justify-between pl-3 text-xs text-[var(--muted)]">
                                  <span>
                                    {d.device} {d.size_gib} GiB → {d.mapped_to ?? "?"}
                                    {d.note ? ` (${d.note})` : ""}
                                  </span>
                                  <span className="tabular-nums">{money(d.monthly_usd)}</span>
                                </div>
                              ))}
                              <CostLines label="Source compute" v={it.cost?.source.compute_monthly_usd} money={money} />
                              <div className="mt-2 flex justify-between border-t border-[var(--border)] pt-1">
                                <span>One-time transfer ({it.cost?.one_time.egress_gib} GiB)</span>
                                <span className="tabular-nums">{money(it.cost?.one_time.egress_usd)}</span>
                              </div>
                            </div>
                            <div>
                              <div className="mb-1 text-xs font-semibold tracking-wide text-[var(--muted)] uppercase">Assumptions</div>
                              <ul className="list-disc space-y-1 pl-4 text-[var(--muted)]">
                                {(it.cost?.assumptions ?? []).map((a, i) => (
                                  <li key={i}>{a}</li>
                                ))}
                              </ul>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </Table>
          </Card>
        </>
      )}
    </>
  );
}

function CostLines({ label, v, money }: { label: string; v: Money | undefined; money: (x: number | null | undefined) => string }) {
  return (
    <div className="flex justify-between py-0.5">
      <span>{label}</span>
      <span className="text-right tabular-nums">
        {money(v?.on_demand)} <span className="text-xs text-[var(--muted)]">/ {money(v?.reserved_1y)} 1y / {money(v?.reserved_3y)} 3y</span>
      </span>
    </div>
  );
}
