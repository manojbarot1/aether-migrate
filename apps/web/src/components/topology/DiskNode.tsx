/**
 * DiskNode — gray cylinder-shaped custom React Flow node.
 */

import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { TopologyNode } from '../../api/topology'

const DiskNode: React.FC<NodeProps> = ({ data, selected }) => {
  const node = data as unknown as TopologyNode & { onClick?: () => void }
  return (
    <div
      onClick={node.onClick}
      style={{
        background: node.is_root ? '#374151' : '#6b7280',
        color: '#fff',
        border: selected ? '2px solid #fbbf24' : '2px solid #4b5563',
        borderRadius: '0 0 50% 50% / 0 0 20px 20px',
        borderTop: '8px solid rgba(255,255,255,0.2)',
        padding: '10px 14px',
        minWidth: '110px',
        cursor: 'pointer',
        fontSize: '12px',
        fontWeight: 500,
        textAlign: 'center',
      }}
    >
      <svg width="16" height="16" viewBox="0 0 16 16" style={{ display: 'block', margin: '0 auto 4px' }}>
        <ellipse cx="8" cy="4" rx="6" ry="2.5" fill="none" stroke="#fff" strokeWidth="1.5" />
        <line x1="2" y1="4" x2="2" y2="12" stroke="#fff" strokeWidth="1.5" />
        <line x1="14" y1="4" x2="14" y2="12" stroke="#fff" strokeWidth="1.5" />
        <ellipse cx="8" cy="12" rx="6" ry="2.5" fill="none" stroke="#fff" strokeWidth="1.5" />
      </svg>
      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '130px' }}>
        {node.name || node.native_id}
      </div>
      <div style={{ fontSize: '10px', opacity: 0.8, marginTop: '2px' }}>disk</div>
      <Handle type="target" position={Position.Left} style={{ background: '#fff' }} />
      <Handle type="source" position={Position.Right} style={{ background: '#fff' }} />
    </div>
  )
}

export default DiskNode
