import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";
import { Badge, Card, Code, ErrorBanner, PageHeader, Spinner, StatusBadge, Table, relativeTime } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useApi, useWorkspace } from "../lib/context";
import { gib } from "../lib/format";

type Obj = Record<string, unknown>;
const str = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));

function Fact({ label, value, provenance }: { label: string; value: ReactNode; provenance?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-[var(--border)] py-1.5 text-sm last:border-0">
      <dt className="text-[var(--muted)]">{label}</dt>
      <dd className="text-right">
        {value}
        {provenance === "inferred" && (
          <span className="ml-2" title="Inferred from the image name; verify before relying on it">
            <Badge tone="warn">inferred</Badge>
          </span>
        )}
      </dd>
    </div>
  );
}

const KIND_LABEL: Record<string, string> = {
  "out:in_subnet": "In subnet",
  "out:protected_by": "Protected by",
  "out:in_network": "In network",
  "out:attached_to": "Attached to",
  "out:routes_to": "Routes to",
  "out:references": "References",
  "in:attached_to": "Attached",
  "in:routes_to": "Behind load balancer",
  "in:in_subnet": "Contains",
  "in:in_network": "Contains",
  "in:protected_by": "Protects",
  "in:references": "Referenced by",
};

export function ResourceDetail() {
  const api = useApi();
  const { workspaceId } = useWorkspace();
  const { resourceId = "" } = useParams();
  const q = useQuery({ queryKey: ["resource", workspaceId, resourceId], queryFn: () => api.resource(workspaceId, resourceId) });

  if (q.isLoading) return <Spinner />;
  if (q.error || !q.data) return <ErrorBanner error={q.error ? errorMessage(q.error) : "Not found"} />;
  const r = q.data;
  const spec = r.spec as Obj;
  const prov = (spec.provenance ?? {}) as Record<string, string>;
  const disks = (spec.disks ?? []) as Obj[];
  const nics = (spec.nics ?? []) as Obj[];
  const rules = (spec.rules ?? []) as Obj[];

  const groups = new Map<string, typeof r.neighbours>();
  for (const n of r.neighbours) {
    const key = `${n.direction}:${n.kind}`;
    groups.set(key, [...(groups.get(key) ?? []), n]);
  }

  return (
    <>
      <Link to="../.." relative="path" className="mb-3 inline-flex items-center gap-1 text-sm text-[var(--muted)] hover:text-[var(--text)]">
        <ArrowLeft className="size-4" /> Inventory
      </Link>
      <PageHeader
        title={r.name ?? r.native_id}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono">{r.native_id}</span>
            <Badge tone="accent">{r.provider.toUpperCase()}</Badge>
            <Badge>{r.type}</Badge>
            <span>
              {r.account} · {r.region}
              {r.zone ? ` / ${r.zone}` : ""}
            </span>
          </span>
        }
        actions={<StatusBadge status={r.status ?? "unknown"} />}
      />
      <p className="-mt-3 mb-5 text-xs text-[var(--muted)]">
        From snapshot {r.snapshot.id.slice(0, 8)} ({r.snapshot.status}), discovered {relativeTime(r.discovered_at)}.
      </p>

      <div className="grid gap-4 lg:grid-cols-2">
        {r.type === "vm" && (
          <>
            <Card title="Compute">
              <dl>
                <Fact label="Instance type" value={<span className="font-mono">{str(spec.source_sku)}</span>} />
                <Fact label="vCPU" value={str(spec.vcpu)} />
                <Fact label="Memory" value={gib(spec.memory_mib as number | null)} />
                <Fact label="Architecture" value={str(spec.cpu_arch)} />
                <Fact label="CPU vendor" value={str(spec.cpu_vendor)} />
                <Fact label="GPU" value={spec.gpu_count ? `${spec.gpu_count} × ${str(spec.gpu_model)}` : "none"} />
                <Fact label="Lifecycle" value={str(spec.lifecycle)} />
                <Fact label="Tenancy" value={str(spec.tenancy)} />
                <Fact label="Network" value={str(spec.network_performance)} />
              </dl>
            </Card>
            <Card title="Operating system & boot">
              <dl>
                <Fact label="Family" value={str(spec.os_family)} provenance={prov.os_family} />
                <Fact label="Distribution" value={str(spec.os_distribution)} provenance={prov.os_distribution} />
                <Fact label="Version" value={str(spec.os_version)} provenance={prov.os_version} />
                <Fact label="Licence" value={str(spec.license_model)} />
                <Fact label="Platform details" value={str(spec.platform_details)} />
                <Fact label="Boot mode" value={str(spec.boot_mode)} />
                <Fact label="Image" value={<span className="font-mono text-xs">{str(spec.image_name ?? spec.image_ref)}</span>} />
                <Fact label="Instance identity" value={<span className="font-mono text-xs break-all">{str(spec.instance_identity)}</span>} />
              </dl>
            </Card>
            <Card title={`Disks (${disks.length})`} className="lg:col-span-2">
              <Table head={["Device", "Volume", "Size", "Type", "IOPS", "Throughput", "Flags"]}>
                {disks.map((d, i) => (
                  <tr key={i} className="border-b border-[var(--border)]">
                    <td className="px-3 py-2 font-mono text-xs">{str(d.device)}</td>
                    <td className="px-3 py-2 font-mono text-xs">{str(d.native_id)}</td>
                    <td className="px-3 py-2 tabular-nums">{d.size_gib ? `${d.size_gib} GiB` : "—"}</td>
                    <td className="px-3 py-2">{str(d.type_class)}</td>
                    <td className="px-3 py-2 tabular-nums">{str(d.iops)}</td>
                    <td className="px-3 py-2 tabular-nums">{d.throughput_mbps ? `${d.throughput_mbps} MB/s` : "—"}</td>
                    <td className="space-x-1 px-3 py-2">
                      {Boolean(d.boot) && <Badge tone="accent">boot</Badge>}
                      {Boolean(d.encrypted) && <Badge tone="ok">encrypted</Badge>}
                      {Boolean(d.ephemeral) && (
                        <span title="Instance-store data does not survive migration">
                          <Badge tone="warn">ephemeral</Badge>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </Table>
            </Card>
            <Card title={`Network interfaces (${nics.length})`} className="lg:col-span-2">
              <Table head={["Interface", "Subnet", "Private IPs", "Public IPs", "Security groups"]}>
                {nics.map((n, i) => (
                  <tr key={i} className="border-b border-[var(--border)] align-top">
                    <td className="px-3 py-2 font-mono text-xs">{str(n.native_id)}</td>
                    <td className="px-3 py-2 font-mono text-xs">{str(n.subnet_native_id)}</td>
                    <td className="px-3 py-2 font-mono text-xs">{((n.private_ips ?? []) as string[]).join(", ") || "—"}</td>
                    <td className="px-3 py-2 font-mono text-xs">{((n.public_ips ?? []) as string[]).join(", ") || "—"}</td>
                    <td className="px-3 py-2 font-mono text-xs">{((n.security_group_native_ids ?? []) as string[]).join(", ") || "—"}</td>
                  </tr>
                ))}
              </Table>
            </Card>
          </>
        )}
        {r.type === "security_group" && (
          <Card title={`Rules (${rules.length})`} className="lg:col-span-2">
            <Table head={["Direction", "Protocol", "Ports", "Peer", "Description"]}>
              {rules.map((x, i) => (
                <tr key={i} className="border-b border-[var(--border)]">
                  <td className="px-3 py-2">{str(x.direction)}</td>
                  <td className="px-3 py-2">{str(x.protocol)}</td>
                  <td className="px-3 py-2 tabular-nums">{x.port_from == null ? "all" : x.port_from === x.port_to ? str(x.port_from) : `${str(x.port_from)}–${str(x.port_to)}`}</td>
                  <td className="px-3 py-2 font-mono text-xs">{str(x.peer_cidr ?? x.peer_group_native_id ?? x.peer_prefix_list)}</td>
                  <td className="px-3 py-2 text-[var(--muted)]">{str(x.description)}</td>
                </tr>
              ))}
            </Table>
          </Card>
        )}
        <Card title="Relationships">
          {r.neighbours.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">No relationships discovered.</p>
          ) : (
            <dl className="space-y-3 text-sm">
              {[...groups.entries()].map(([key, items]) => (
                <div key={key}>
                  <dt className="mb-1 text-xs uppercase tracking-wide text-[var(--muted)]">{KIND_LABEL[key] ?? key}</dt>
                  <dd className="flex flex-wrap gap-2">
                    {items.map((n) => (
                      <Link key={n.resource.id} to={`../${n.resource.id}`} relative="path" className="rounded-md border border-[var(--border)] px-2 py-1 hover:border-[var(--accent)]">
                        <span className="text-[var(--muted)]">{n.resource.type}</span> {n.resource.name ?? n.resource.native_id}
                      </Link>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </Card>
        <Card title="Tags">
          {Object.keys(r.tags).length === 0 ? (
            <p className="text-sm text-[var(--muted)]">No tags.</p>
          ) : (
            <dl>
              {Object.entries(r.tags).map(([k, v]) => (
                <Fact key={k} label={k} value={<span className="font-mono text-xs">{v}</span>} />
              ))}
            </dl>
          )}
        </Card>
        <Card title="Provider payload" className="lg:col-span-2">
          <details>
            <summary className="cursor-pointer text-sm text-[var(--muted)]">Raw API response as discovered (for audit and troubleshooting)</summary>
            <div className="mt-2">
              <Code>{JSON.stringify(r.raw, null, 2)}</Code>
            </div>
          </details>
        </Card>
      </div>
    </>
  );
}
