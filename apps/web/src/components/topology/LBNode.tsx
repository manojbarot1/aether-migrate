/**
 * LBNode — purple rounded-rectangle custom React Flow node for load balancers.
 */

import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { TopologyNode } from '../../api/topology'

const LBNode: React.FC<NodeProps> = ({ data, selected }) => {
  const node = data as unknown as TopologyNode & { onClick?: () => void }
  return (
    <div
      onClick={node.onClick}
      style={{
        background: node.is_root ? '#4c1d95' : '#7c3aed',
        color: '#fff',
        border: selected ? '2px solid #fbbf24' : '2px solid #6d28d9',
        borderRadius: '20px',
        padding: '8px 18px',
        minWidth: '120px',
        cursor: 'pointer',
        fontSize: '12px',
        fontWeight: 500,
        textAlign: 'center',
      }}
    >
      <svg width="16" height="16" viewBox="0 0 16 16" style={{ display: 'block', margin: '0 auto 4px' }}>
        <rect x="1" y="5" width="14" height="6" rx="3" fill="none" stroke="#fff" strokeWidth="1.5" />
        <line x1="5" y1="8" x2="11" y2="8" stroke="#fff" strokeWidth="1.5" />
        <polyline points="8,5 8,3 5,3" fill="none" stroke="#fff" strokeWidth="1.2" />
        <polyline points="8,11 8,13 11,13" fill="none" stroke="#fff" strokeWidth="1.2" />
      </svg>
      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '140px' }}>
        {node.name || node.native_id}
      </div>
      <div style={{ fontSize: '10px', opacity: 0.8, marginTop: '2px' }}>load balancer</div>
      <Handle type="target" position={Position.Left} style={{ background: '#fff' }} />
      <Handle type="source" position={Position.Right} style={{ background: '#fff' }} />
    </div>
  )
}

export default LBNode
