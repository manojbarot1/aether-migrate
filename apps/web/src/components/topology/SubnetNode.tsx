/**
 * SubnetNode — green hexagon-shaped custom React Flow node.
 * Approximated with a clip-path hexagon since Mermaid {{}} isn't CSS-native.
 */

import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { TopologyNode } from '../../api/topology'

const SubnetNode: React.FC<NodeProps> = ({ data, selected }) => {
  const node = data as unknown as TopologyNode & { onClick?: () => void }
  return (
    <div
      onClick={node.onClick}
      style={{
        background: node.is_root ? '#14532d' : '#16a34a',
        color: '#fff',
        border: selected ? '2px solid #fbbf24' : '2px solid #15803d',
        borderRadius: '12px 4px 12px 4px',
        padding: '8px 14px',
        minWidth: '120px',
        cursor: 'pointer',
        fontSize: '12px',
        fontWeight: 500,
        textAlign: 'center',
      }}
    >
      <svg width="16" height="16" viewBox="0 0 16 16" style={{ display: 'block', margin: '0 auto 4px' }}>
        <polygon
          points="8,1 15,4.5 15,11.5 8,15 1,11.5 1,4.5"
          fill="none"
          stroke="#fff"
          strokeWidth="1.5"
        />
      </svg>
      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '140px' }}>
        {node.name || node.native_id}
      </div>
      <div style={{ fontSize: '10px', opacity: 0.8, marginTop: '2px' }}>subnet</div>
      <Handle type="target" position={Position.Left} style={{ background: '#fff' }} />
      <Handle type="source" position={Position.Right} style={{ background: '#fff' }} />
    </div>
  )
}

export default SubnetNode
