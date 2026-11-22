import React, { useState } from 'react';
import { BaseEdge, EdgeLabelRenderer, getBezierPath } from '@xyflow/react';

// Reference motion communicates direction, never a provider task's running state.
export default function CreativeEdge({ id, sourceX, sourceY, targetX, targetY,
  sourcePosition, targetPosition, markerEnd, label, data, style }) {
  const [hovered, setHovered] = useState(false);
  const active = hovered || Boolean(data?.emphasized);
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY,
    sourcePosition, targetPosition, curvature: .36 });
  const color = style?.stroke || '#8c9db7';
  const opacity = active ? 1 : style?.opacity ?? .18;
  const duration = Math.min(4, Math.max(2.2, Math.hypot(targetX - sourceX, targetY - sourceY) / 115));
  const showLabel = active;
  return <>
    <g className={`creative-edge ${active ? 'is-active' : ''}`} style={{ opacity }}
      onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
      {active && <path className="creative-edge-halo" d={path} fill="none" stroke={color} strokeWidth="6" opacity=".055"/>}
      <BaseEdge id={id} path={path} markerEnd={markerEnd} interactionWidth={20}
        style={{ ...style, opacity: 1, strokeWidth: active ? 2.2 : 1.2,
          strokeLinecap: 'round', strokeLinejoin: 'round' }}/>
      {active && <g className="creative-edge-particle" pointerEvents="none">
        <circle r="4.1" fill={color} opacity=".13"/>
        <circle r="1.9" fill={color} opacity=".92"/>
        <animateMotion dur={`${duration}s`} repeatCount="indefinite" path={path}/>
      </g>}
    </g>
    {label && <EdgeLabelRenderer><div className={`creative-edge-label ${showLabel ? 'is-visible' : ''}`}
      style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}>
      {label}
    </div></EdgeLabelRenderer>}
  </>;
}
