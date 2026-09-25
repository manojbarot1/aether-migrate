/**
 * NicNode — light-blue circle custom React Flow node for network interfaces.
 */

import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { TopologyNode } from '../../api/topology'

const NicNode: React.FC<NodeProps> = ({ data, selected }) => {
  const node = data as unknown as TopologyNode & { onClick?: () => void }
  return (
    <div
      onClick={node.onClick}
      style={{
        background: node.is_root ? '#0369a1' : '#0ea5e9',
        color: '#fff',
        border: selected ? '2px solid #fbbf24' : '2px solid #0284c7',
        borderRadius: '50%',
        width: '90px',
        height: '90px',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        cursor: 'pointer',
        fontSize: '11px',
        fontWeight: 500,
        textAlign: 'center',
      }}
    >
      <svg width="14" height="14" viewBox="0 0 14 14" style={{ display: 'block', marginBottom: '3px' }}>
        <circle cx="7" cy="7" r="5.5" fill="none" stroke="#fff" strokeWidth="1.5" />
        <line x1="7" y1="1.5" x2="7" y2="12.5" stroke="#fff" strokeWidth="1" />
        <ellipse cx="7" cy="7" rx="2.5" ry="5.5" fill="none" stroke="#fff" strokeWidth="1" />
      </svg>
      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '78px' }}>
        {node.name || node.native_id}
      </div>
      <Handle type="target" position={Position.Left} style={{ background: '#fff' }} />
      <Handle type="source" position={Position.Right} style={{ background: '#fff' }} />
    </div>
  )
}

export default NicNode
