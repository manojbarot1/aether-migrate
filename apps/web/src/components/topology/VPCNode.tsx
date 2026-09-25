/**
 * VPCNode — dark-green large container node for VPCs / networks.
 */

import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { TopologyNode } from '../../api/topology'

const VPCNode: React.FC<NodeProps> = ({ data, selected }) => {
  const node = data as unknown as TopologyNode & { onClick?: () => void }
  return (
    <div
      onClick={node.onClick}
      style={{
        background: node.is_root ? '#14532d' : '#166534',
        color: '#fff',
        border: selected ? '2px solid #fbbf24' : '2px dashed #15803d',
        borderRadius: '8px',
        padding: '10px 18px',
        minWidth: '140px',
        cursor: 'pointer',
        fontSize: '12px',
        fontWeight: 500,
        textAlign: 'center',
      }}
    >
      <svg width="16" height="16" viewBox="0 0 16 16" style={{ display: 'block', margin: '0 auto 4px' }}>
        <rect x="1" y="1" width="14" height="14" rx="2" fill="none" stroke="#fff" strokeWidth="1.5" strokeDasharray="3,2" />
        <rect x="4" y="4" width="8" height="8" rx="1" fill="none" stroke="#fff" strokeWidth="1" />
      </svg>
      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '160px' }}>
        {node.name || node.native_id}
      </div>
      <div style={{ fontSize: '10px', opacity: 0.8, marginTop: '2px' }}>vpc / network</div>
      <Handle type="target" position={Position.Left} style={{ background: '#fff' }} />
      <Handle type="source" position={Position.Right} style={{ background: '#fff' }} />
    </div>
  )
}

export default VPCNode
