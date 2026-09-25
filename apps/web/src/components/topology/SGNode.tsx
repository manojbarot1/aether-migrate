/**
 * SGNode — orange diamond custom React Flow node for security groups.
 */

import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { TopologyNode } from '../../api/topology'

const SGNode: React.FC<NodeProps> = ({ data, selected }) => {
  const node = data as unknown as TopologyNode & { onClick?: () => void }
  return (
    <div
      onClick={node.onClick}
      style={{
        background: node.is_root ? '#92400e' : '#d97706',
        color: '#fff',
        border: selected ? '2px solid #fbbf24' : '2px solid #b45309',
        transform: 'rotate(45deg)',
        padding: '10px',
        width: '90px',
        height: '90px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        cursor: 'pointer',
        fontSize: '11px',
        fontWeight: 500,
      }}
    >
      <div style={{ transform: 'rotate(-45deg)', textAlign: 'center', width: '100%' }}>
        <svg width="14" height="14" viewBox="0 0 14 14" style={{ display: 'block', margin: '0 auto 3px' }}>
          <path d="M7 1 L13 7 L7 13 L1 7 Z" fill="none" stroke="#fff" strokeWidth="1.5" />
        </svg>
        <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '80px' }}>
          {node.name || node.native_id}
        </div>
      </div>
      <Handle type="target" position={Position.Left} style={{ background: '#fff', transform: 'rotate(-45deg)' }} />
      <Handle type="source" position={Position.Right} style={{ background: '#fff', transform: 'rotate(-45deg)' }} />
    </div>
  )
}

export default SGNode
