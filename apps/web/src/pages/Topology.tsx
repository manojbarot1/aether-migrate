/**
 * Topology page — interactive infrastructure graph viewer.
 *
 * Features:
 *  - Resource search bar (loads topology on selection)
 *  - React Flow canvas with custom nodes per resource kind
 *  - ELK.js auto-layout (hierarchical, runs async)
 *  - Depth selector (1 / 2 / 3)
 *  - Source / Target toggle (Target shows Phase 7 placeholder)
 *  - "Export Mermaid" button — copies to clipboard
 *  - Right-side info panel on node click
 *  - Minimap (bottom-right) and Controls (zoom/fit, bottom-left)
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { fetchResourceSubgraph, exportMermaid } from '../api/topology'
import type { TopologyNode as ApiNode, TopologyEdge as ApiEdge } from '../api/topology'

import VMNode from '../components/topology/VMNode'
import SubnetNode from '../components/topology/SubnetNode'
import SGNode from '../components/topology/SGNode'
import DiskNode from '../components/topology/DiskNode'
import LBNode from '../components/topology/LBNode'
import NicNode from '../components/topology/NicNode'
import VPCNode from '../components/topology/VPCNode'
import TopologyEdgeComponent from '../components/topology/TopologyEdge'
import { useTopologyLayout } from '../components/topology/useTopologyLayout'

// ---------------------------------------------------------------------------
// React Flow node + edge type registrations
// ---------------------------------------------------------------------------

const nodeTypes = {
  vm: VMNode,
  disk: DiskNode,
  nic: NicNode,
  subnet: SubnetNode,
  security_group: SGNode,
  load_balancer: LBNode,
  lb: LBNode,
  network: VPCNode,
  vpc: VPCNode,
  default: VMNode,
}

const edgeTypes = {
  topology: TopologyEdgeComponent,
}

// ---------------------------------------------------------------------------
// Conversion helpers
// ---------------------------------------------------------------------------

function toFlowNode(apiNode: ApiNode, onNodeClick: (n: ApiNode) => void): Node {
  return {
    id: apiNode.id,
    type: nodeTypes[apiNode.kind as keyof typeof nodeTypes] ? apiNode.kind : 'default',
    position: { x: 0, y: 0 }, // ELK will override this
    data: { ...apiNode, onClick: () => onNodeClick(apiNode) },
  }
}

function toFlowEdge(apiEdge: ApiEdge): Edge {
  return {
    id: `${apiEdge.from_id}--${apiEdge.to_id}--${apiEdge.kind}`,
    source: apiEdge.from_id,
    target: apiEdge.to_id,
    type: 'topology',
    data: { label: apiEdge.label },
    markerEnd: { type: 'arrowclosed' as const },
  }
}

// ---------------------------------------------------------------------------
// Info panel
// ---------------------------------------------------------------------------

interface InfoPanelProps {
  node: ApiNode
  onClose: () => void
}

const InfoPanel: React.FC<InfoPanelProps> = ({ node, onClose }) => (
  <div
    style={{
      position: 'absolute',
      top: 0,
      right: 0,
      width: '280px',
      height: '100%',
      background: '#fff',
      borderLeft: '1px solid #e5e7eb',
      padding: '1.25rem',
      overflowY: 'auto',
      zIndex: 10,
    }}
  >
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1rem' }}>
      <div>
        <div style={{ fontWeight: 700, fontSize: '0.95rem', color: '#1f2328' }}>{node.name || node.native_id}</div>
        <div style={{ fontSize: '0.75rem', color: '#57606a', marginTop: '2px' }}>{node.kind} · {node.provider}</div>
      </div>
      <button
        onClick={onClose}
        style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '1rem', color: '#57606a' }}
      >
        ✕
      </button>
    </div>

    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
      <tbody>
        {[
          ['Region', node.region],
          ['Status', node.status],
          ['Provider', node.provider],
          ['Native ID', node.native_id],
          ...Object.entries(node.metadata).map(([k, v]) => [k, String(v)]),
        ].map(([k, v]) => (
          <tr key={k} style={{ borderBottom: '1px solid #f3f4f6' }}>
            <td style={{ padding: '5px 0', color: '#57606a', fontWeight: 500, width: '45%' }}>{k}</td>
            <td style={{ padding: '5px 0', color: '#1f2328', wordBreak: 'break-all' }}>{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
)

// ---------------------------------------------------------------------------
// Inner canvas (must be inside ReactFlowProvider)
// ---------------------------------------------------------------------------

interface CanvasProps {
  apiNodes: ApiNode[]
  apiEdges: ApiEdge[]
  selectedNode: ApiNode | null
  onNodeSelect: (n: ApiNode) => void
  onClosePanel: () => void
}

const TopologyCanvas: React.FC<CanvasProps> = ({
  apiNodes,
  apiEdges,
  selectedNode,
  onNodeSelect,
  onClosePanel,
}) => {
  const rawNodes: Node[] = useMemo(
    () => apiNodes.map((n) => toFlowNode(n, onNodeSelect)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [apiNodes],
  )
  const rawEdges: Edge[] = useMemo(() => apiEdges.map(toFlowEdge), [apiEdges])

  const { nodes: layoutNodes, edges: layoutEdges, layoutDone } = useTopologyLayout(rawNodes, rawEdges)

  const [nodes, setNodes, onNodesChange] = useNodesState(layoutNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(layoutEdges)

  // Sync layout result into React Flow state
  useEffect(() => {
    if (layoutDone) {
      setNodes(layoutNodes)
      setEdges(layoutEdges)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutDone, layoutNodes, layoutEdges])

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge(params, eds)),
    [setEdges],
  )

  return (
    <div style={{ position: 'relative', flex: 1, height: '100%' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.2}
        maxZoom={2}
      >
        <Background color="#e5e7eb" gap={20} />
        <Controls position="bottom-left" />
        <MiniMap position="bottom-right" nodeColor={(n) => {
          const kind = (n.data as unknown as ApiNode).kind
          const palette: Record<string, string> = {
            vm: '#3b82f6',
            subnet: '#16a34a',
            security_group: '#d97706',
            disk: '#6b7280',
            nic: '#0ea5e9',
            load_balancer: '#7c3aed',
            lb: '#7c3aed',
            network: '#166534',
            vpc: '#166534',
          }
          return palette[kind] ?? '#9ca3af'
        }} />
      </ReactFlow>

      {selectedNode && (
        <InfoPanel node={selectedNode} onClose={onClosePanel} />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

const Topology: React.FC = () => {
  const [resourceIdInput, setResourceIdInput] = useState('')
  const [activeResourceId, setActiveResourceId] = useState<string | null>(null)
  const [depth, setDepth] = useState<1 | 2 | 3>(2)
  const [viewMode, setViewMode] = useState<'source' | 'target'>('source')
  const [apiNodes, setApiNodes] = useState<ApiNode[]>([])
  const [apiEdges, setApiEdges] = useState<ApiEdge[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [truncated, setTruncated] = useState(false)
  const [selectedNode, setSelectedNode] = useState<ApiNode | null>(null)
  const [copyMsg, setCopyMsg] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const loadTopology = useCallback(async (rid: string, d: number) => {
    if (!rid.trim()) return
    setLoading(true)
    setError(null)
    setSelectedNode(null)
    abortRef.current?.abort()
    abortRef.current = new AbortController()
    try {
      const resp = await fetchResourceSubgraph(rid.trim(), d)
      setApiNodes(resp.nodes)
      setApiEdges(resp.edges)
      setTruncated(resp.truncated)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to load topology'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }, [])

  const handleSearch = () => {
    if (!resourceIdInput.trim()) return
    setActiveResourceId(resourceIdInput.trim())
    loadTopology(resourceIdInput.trim(), depth)
  }

  const handleDepthChange = (d: 1 | 2 | 3) => {
    setDepth(d)
    if (activeResourceId) loadTopology(activeResourceId, d)
  }

  const handleExportMermaid = async () => {
    if (!activeResourceId) return
    try {
      const text = await exportMermaid(activeResourceId, depth)
      await navigator.clipboard.writeText(text)
      setCopyMsg('Copied to clipboard!')
      setTimeout(() => setCopyMsg(null), 2500)
    } catch {
      setCopyMsg('Copy failed')
      setTimeout(() => setCopyMsg(null), 2500)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 4rem)' }}>
      {/* ── Toolbar ── */}
      <div style={{
        display: 'flex',
        gap: '0.75rem',
        alignItems: 'center',
        padding: '0.75rem 1rem',
        borderBottom: '1px solid #e5e7eb',
        flexWrap: 'wrap',
        background: '#f7f8fa',
      }}>
        {/* Resource ID search */}
        <input
          type="text"
          placeholder="Paste resource UUID…"
          value={resourceIdInput}
          onChange={(e) => setResourceIdInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          style={{
            flex: '1 1 260px',
            padding: '0.4rem 0.75rem',
            border: '1px solid #e5e7eb',
            borderRadius: '4px',
            fontSize: '0.875rem',
            fontFamily: 'monospace',
          }}
        />
        <button
          onClick={handleSearch}
          disabled={loading}
          style={{
            padding: '0.4rem 1rem',
            background: '#3b82d4',
            color: '#fff',
            border: 'none',
            borderRadius: '4px',
            cursor: loading ? 'not-allowed' : 'pointer',
            fontSize: '0.875rem',
          }}
        >
          {loading ? 'Loading…' : 'Load'}
        </button>

        {/* Depth selector */}
        <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
          <span style={{ fontSize: '0.8rem', color: '#57606a' }}>Depth:</span>
          {([1, 2, 3] as const).map((d) => (
            <button
              key={d}
              onClick={() => handleDepthChange(d)}
              style={{
                padding: '0.3rem 0.6rem',
                background: depth === d ? '#3b82d4' : '#fff',
                color: depth === d ? '#fff' : '#1f2328',
                border: '1px solid #e5e7eb',
                borderRadius: '4px',
                cursor: 'pointer',
                fontSize: '0.8rem',
              }}
            >
              {d}
            </button>
          ))}
        </div>

        {/* Source / Target toggle */}
        <div style={{ display: 'flex', gap: '4px' }}>
          {(['source', 'target'] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => setViewMode(mode)}
              style={{
                padding: '0.3rem 0.8rem',
                background: viewMode === mode ? '#1f2328' : '#fff',
                color: viewMode === mode ? '#fff' : '#1f2328',
                border: '1px solid #e5e7eb',
                borderRadius: '4px',
                cursor: 'pointer',
                fontSize: '0.8rem',
                textTransform: 'capitalize',
              }}
            >
              {mode}
            </button>
          ))}
        </div>

        {/* Export Mermaid */}
        <button
          onClick={handleExportMermaid}
          disabled={!activeResourceId}
          style={{
            padding: '0.3rem 0.9rem',
            background: '#fff',
            color: activeResourceId ? '#1f2328' : '#9ca3af',
            border: '1px solid #e5e7eb',
            borderRadius: '4px',
            cursor: activeResourceId ? 'pointer' : 'not-allowed',
            fontSize: '0.8rem',
          }}
        >
          {copyMsg ?? 'Export Mermaid'}
        </button>

        {truncated && (
          <span style={{ fontSize: '0.75rem', color: '#d97706' }}>
            Graph truncated to 100 nodes
          </span>
        )}
      </div>

      {/* ── Canvas area ── */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        {viewMode === 'target' ? (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            height: '100%',
            color: '#57606a',
            flexDirection: 'column',
            gap: '0.5rem',
          }}>
            <div style={{ fontSize: '1.1rem', fontWeight: 600 }}>Target Topology</div>
            <div style={{ fontSize: '0.875rem' }}>Available in Phase 7</div>
          </div>
        ) : error ? (
          <div style={{ padding: '2rem', color: '#b91c1c', fontSize: '0.875rem' }}>{error}</div>
        ) : apiNodes.length === 0 && !loading ? (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            height: '100%',
            color: '#9ca3af',
            fontSize: '0.875rem',
          }}>
            Enter a resource UUID above and click Load to visualise its topology.
          </div>
        ) : (
          <ReactFlowProvider>
            <TopologyCanvas
              apiNodes={apiNodes}
              apiEdges={apiEdges}
              selectedNode={selectedNode}
              onNodeSelect={setSelectedNode}
              onClosePanel={() => setSelectedNode(null)}
            />
          </ReactFlowProvider>
        )}
      </div>
    </div>
  )
}

export default Topology
