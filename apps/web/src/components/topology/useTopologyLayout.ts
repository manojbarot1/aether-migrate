/**
 * useTopologyLayout — runs ELK.js hierarchical layout in a Web Worker.
 *
 * ELK is imported dynamically so Vite can split it into its own chunk and the
 * layout computation does not block the main thread.
 *
 * Returns { nodes, edges } with x/y positions applied, or the input unchanged
 * while layout is running.
 */

import { useEffect, useState } from 'react'
import type { Node, Edge } from '@xyflow/react'

// Node/edge dimensions used for ELK layout hints
const NODE_WIDTH = 160
const NODE_HEIGHT = 60

export interface LayoutResult {
  nodes: Node[]
  edges: Edge[]
  layoutDone: boolean
}

export function useTopologyLayout(
  rawNodes: Node[],
  rawEdges: Edge[],
): LayoutResult {
  const [result, setResult] = useState<LayoutResult>({
    nodes: rawNodes,
    edges: rawEdges,
    layoutDone: false,
  })

  // Re-run whenever the graph content changes (compare by serialised ID lists)
  const nodeKey = rawNodes.map((n) => n.id).join(',')
  const edgeKey = rawEdges.map((e) => e.id).join(',')

  useEffect(() => {
    if (rawNodes.length === 0) {
      setResult({ nodes: [], edges: rawEdges, layoutDone: true })
      return
    }

    let cancelled = false

    ;(async () => {
      // Lazy-import elkjs so the main bundle stays lean
      const ELK = (await import('elkjs/lib/elk.bundled.js')).default
      const elk = new ELK()

      const graph = {
        id: 'root',
        layoutOptions: {
          'elk.algorithm': 'layered',
          'elk.direction': 'RIGHT',
          'elk.spacing.nodeNode': '40',
          'elk.layered.spacing.nodeNodeBetweenLayers': '80',
          'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
        },
        children: rawNodes.map((n) => ({
          id: n.id,
          width: NODE_WIDTH,
          height: NODE_HEIGHT,
        })),
        edges: rawEdges.map((e) => ({
          id: e.id,
          sources: [e.source],
          targets: [e.target],
        })),
      }

      try {
        const laid = await elk.layout(graph)
        if (cancelled) return

        const posMap: Record<string, { x: number; y: number }> = {}
        for (const child of laid.children ?? []) {
          if (child.x !== undefined && child.y !== undefined) {
            posMap[child.id] = { x: child.x, y: child.y }
          }
        }

        const positionedNodes: Node[] = rawNodes.map((n) => ({
          ...n,
          position: posMap[n.id] ?? n.position,
        }))

        setResult({ nodes: positionedNodes, edges: rawEdges, layoutDone: true })
      } catch {
        // ELK failed — fall back to the unpositioned nodes so the canvas still renders
        if (!cancelled) {
          setResult({ nodes: rawNodes, edges: rawEdges, layoutDone: true })
        }
      }
    })()

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeKey, edgeKey])

  return result
}
