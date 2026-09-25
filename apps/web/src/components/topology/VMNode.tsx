/**
 * VMNode — blue rectangle custom React Flow node for virtual machines.
 */

import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { TopologyNode } from '../../api/topology'

const VMNode: React.FC<NodeProps> = ({ data, selected }) => {
  const node = data as unknown as TopologyNode & { onClick?: () => void }
  return (
    <div
      onClick={node.onClick}
      style={{
        background: node.is_root ? '#1d4ed8' : '#3b82f6',
        color: '#fff',
        border: selected ? '2px solid #fbbf24' : '2px solid #1e40af',
        borderRadius: '4px',
        padding: '8px 14px',
        minWidth: '120px',
        cursor: 'pointer',
        fontSize: '12px',
        fontWeight: node.is_root ? 700 : 500,
        textAlign: 'center',
      }}
    >
      <svg width="16" height="16" viewBox="0 0 16 16" style={{ display: 'block', margin: '0 auto 4px' }}>
        <rect x="1" y="3" width="14" height="10" rx="1" fill="none" stroke="#fff" strokeWidth="1.5" />
        <line x1="1" y1="6" x2="15" y2="6" stroke="#fff" strokeWidth="1" />
        <circle cx="3.5" cy="4.5" r="0.8" fill="#fff" />
        <circle cx="6" cy="4.5" r="0.8" fill="#fff" />
      </svg>
      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '140px' }}>
        {node.name || node.native_id}
      </div>
      <div style={{ fontSize: '10px', opacity: 0.8, marginTop: '2px' }}>{node.status}</div>
      <Handle type="target" position={Position.Left} style={{ background: '#fff' }} />
      <Handle type="source" position={Position.Right} style={{ background: '#fff' }} />
    </div>
  )
}

export default VMNode
