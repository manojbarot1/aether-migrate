import "@xyflow/react/dist/style.css";

import { useQuery } from "@tanstack/react-query";
import { Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import ELK, { type ElkNode } from "elkjs/lib/elk.bundled.js";
import { Network, Server, Shield, Split } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { Button, Card, EmptyState, ErrorBanner, PageHeader, Select, Spinner } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useApi, useWorkspace } from "../lib/context";
import type { TopologyNode, TopologyView } from "../lib/types";

const elk = new ELK();
const LEAF = { vm: [190, 54], load_balancer: [200, 54], security_group: [170, 44] } as const;

const ICON = { vm: Server, load_balancer: Split, security_group: Shield } as const;

function LeafNode({ data }: NodeProps<Node<{ n: TopologyNode }>>) {
  const n = data.n;
  const Icon = ICON[n.type as keyof typeof ICON] ?? Server;
  const tone =
    n.type === "security_group" ? "border-dashed" : n.type === "load_balancer" ? "border-[var(--accent)]" : "border-[var(--border)]";
  return (
    <div className={`h-full w-full rounded-md border bg-[var(--panel)] px-2 py-1.5 text-xs shadow-sm ${tone}`}>
      <Handle type="target" position={Position.Left} className="!opacity-0" />
      <div className="flex items-center gap-1.5 font-medium">
        <Icon className="size-3.5 shrink-0 text-[var(--muted)]" aria-hidden />
        <span className="truncate">{n.name ?? n.native_id}</span>
      </div>
      {n.detail && <div className="truncate pl-5 font-mono text-[10px] text-[var(--muted)]">{n.detail}</div>}
      <Handle type="source" position={Position.Right} className="!opacity-0" />
    </div>
  );
}

function GroupNode({ data }: NodeProps<Node<{ n: TopologyNode }>>) {
  const n = data.n;
  const isNet = n.type === "network";
  return (
    <div
      className={`h-full w-full rounded-lg border ${isNet ? "border-[var(--accent)] bg-[color-mix(in_srgb,var(--accent)_4%,transparent)]" : "border-[var(--border)] bg-[color-mix(in_srgb,var(--panel-2)_60%,transparent)]"}`}
    >
      <div className="flex items-center gap-1.5 px-2 py-1 text-xs font-semibold">
        {isNet && <Network className="size-3.5" aria-hidden />}
        {n.name ?? n.native_id}
        {n.detail && <span className="font-mono font-normal text-[var(--muted)]">{n.detail}</span>}
      </div>
    </div>
  );
}

const nodeTypes = { leaf: LeafNode, group: GroupNode };

async function layout(view: TopologyView): Promise<{ nodes: Node[]; edges: Edge[] }> {
  const children = new Map<string | null, TopologyNode[]>();
  for (const n of view.nodes) children.set(n.parent, [...(children.get(n.parent) ?? []), n]);

  const toElk = (n: TopologyNode): ElkNode => {
    const kids = children.get(n.id) ?? [];
    if (n.type === "network" || n.type === "subnet") {
      return {
        id: n.id,
        layoutOptions: {
          "elk.algorithm": "layered",
          "elk.hierarchyHandling": "INCLUDE_CHILDREN",
          "elk.direction": n.type === "network" ? "RIGHT" : "DOWN",
          "elk.padding": "[top=32,left=12,bottom=12,right=12]",
          "elk.spacing.nodeNode": "14",
        },
        children: kids.map(toElk),
        ...(kids.length === 0 ? { width: 180, height: 60 } : {}),
      };
    }
    const [w, h] = LEAF[n.type as keyof typeof LEAF] ?? [180, 50];
    return { id: n.id, width: w, height: h };
  };

  const root: ElkNode = {
    id: "root",
    layoutOptions: { "elk.algorithm": "layered", "elk.hierarchyHandling": "INCLUDE_CHILDREN", "elk.direction": "RIGHT" },
    children: [toElk(view.network)],
    edges: view.edges.map((e, i) => ({ id: `e${i}`, sources: [e.from], targets: [e.to] })),
  };
  const res = await elk.layout(root);

  const byId = new Map(view.nodes.map((n) => [n.id, n]));
  const nodes: Node[] = [];
  const walk = (en: ElkNode, parent?: string) => {
    const n = byId.get(en.id);
    if (n) {
      const group = n.type === "network" || n.type === "subnet";
      nodes.push({
        id: en.id,
        type: group ? "group" : "leaf",
        position: { x: en.x ?? 0, y: en.y ?? 0 },
        data: { n },
        style: { width: en.width, height: en.height },
        ...(parent ? { parentId: parent, extent: "parent" as const } : {}),
        draggable: !group,
        selectable: true,
      });
    }
    for (const c of en.children ?? []) walk(c, n ? en.id : undefined);
  };
  walk(res);

  const edges: Edge[] = view.edges.map((e, i) => ({
    id: `e${i}`,
    source: e.from,
    target: e.to,
    animated: e.kind === "routes_to",
    style: e.kind === "routes_to" ? { stroke: "var(--accent)" } : { strokeDasharray: "4 3", stroke: "var(--muted)" },
    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
    label: e.kind === "references" ? "allows" : undefined,
  }));
  return { nodes, edges };
}

export function Topology() {
  const api = useApi();
  const navigate = useNavigate();
  const { workspaceId } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const network = params.get("network") ?? "";
  const [sgs, setSgs] = useState(false);
  const [copied, setCopied] = useState(false);

  const nets = useQuery({
    queryKey: ["resources", workspaceId, { type: "network" }],
    queryFn: () => api.resources(workspaceId, { type: "network", limit: 500 }),
  });
  useEffect(() => {
    const first = nets.data?.items[0];
    if (!network && first) setParams({ network: first.id }, { replace: true });
  }, [nets.data, network, setParams]);

  const topo = useQuery({
    queryKey: ["topology", workspaceId, network, sgs],
    queryFn: () => api.topology(workspaceId, network, sgs),
    enabled: !!network,
  });
  // Layout is async (ELK) and derived from the fetched view, so it is a query too.
  const laid = useQuery({
    queryKey: ["topology-layout", workspaceId, network, sgs, topo.dataUpdatedAt],
    queryFn: () => layout(topo.data!),
    enabled: !!topo.data,
    staleTime: Infinity,
    retry: false,
  });
  const graph = laid.data ?? null;
  const layoutError = laid.error ? `Layout failed: ${String((laid.error as Error).message).slice(0, 200)}` : null;

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of topo.data?.nodes ?? []) c[n.type] = (c[n.type] ?? 0) + 1;
    return c;
  }, [topo.data]);

  return (
    <>
      <PageHeader
        title="Topology"
        subtitle="Network placement and traffic paths from the latest discovery. Click any machine, load balancer or security group to inspect it."
        actions={
          <>
            <Select aria-label="Network" value={network} onChange={(e) => setParams({ network: e.target.value }, { replace: true })}>
              {(nets.data?.items ?? []).map((n) => (
                <option key={n.id} value={n.id}>
                  {n.name ?? n.native_id} · {n.region}
                </option>
              ))}
            </Select>
            <label className="flex items-center gap-1.5 text-sm whitespace-nowrap">
              <input type="checkbox" checked={sgs} onChange={(e) => setSgs(e.target.checked)} /> Security groups
            </label>
            {topo.data && (
              <Button
                onClick={() =>
                  void navigator.clipboard.writeText(topo.data.mermaid).then(() => {
                    setCopied(true);
                    setTimeout(() => setCopied(false), 1500);
                  })
                }
              >
                {copied ? "Copied" : "Copy Mermaid"}
              </Button>
            )}
          </>
        }
      />
      <ErrorBanner error={layoutError ?? (topo.error ? errorMessage(topo.error) : nets.error ? errorMessage(nets.error) : null)} />
      {topo.data?.truncated && <ErrorBanner error="This network has more machines than can be drawn; showing the first 400." />}
      <Card>
        {!layoutError && (nets.isLoading || (network && (topo.isLoading || !graph))) ? (
          nets.data?.items.length === 0 ? null : <Spinner label="Laying out" />
        ) : null}
        {nets.data?.items.length === 0 && <EmptyState icon={<Network className="size-8" />} title="No networks discovered yet" />}
        {graph && topo.data && (
          <>
            <div className="mb-2 flex flex-wrap gap-3 text-xs text-[var(--muted)]">
              <span>{counts.subnet ?? 0} subnets</span>
              <span>{counts.vm ?? 0} machines</span>
              <span>{counts.load_balancer ?? 0} load balancers</span>
              {sgs && <span>{counts.security_group ?? 0} security groups</span>}
              <span className="ml-auto">— routes to · - - protected by / allows</span>
            </div>
            <div className="h-[70vh] rounded-md border border-[var(--border)]">
              <ReactFlow
                nodes={graph.nodes}
                edges={graph.edges}
                nodeTypes={nodeTypes}
                fitView
                minZoom={0.1}
                proOptions={{ hideAttribution: true }}
                onNodeClick={(_, node) => {
                  const n = (node.data as { n: TopologyNode }).n;
                  if (n.type !== "subnet") navigate(`/w/${workspaceId}/inventory/${n.id}`);
                }}
              >
                <Background gap={16} />
                <Controls showInteractive={false} />
                <MiniMap pannable zoomable />
              </ReactFlow>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
