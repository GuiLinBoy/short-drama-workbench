import React, {useEffect, useId, useRef, useState} from 'react';
import {inspectorWidth} from './responsive-layout.js';

const WIDTH_KEY = 'manju.canvas.inspector.width.v1';
const DEFAULT_WIDTH = 330;
const boundedWidth=inspectorWidth;

export default function ResizableWorkspace({children, inspector}) {
  const workspace = useRef(null), drag = useRef(null), current = useRef(DEFAULT_WIDTH);
  const [available, setAvailable] = useState(1400), [resizing, setResizing] = useState(false);
  const [preferred, setPreferred] = useState(() => {
    try { const saved = Number(localStorage.getItem(WIDTH_KEY)); return saved >= 260 && saved <= 900 ? saved : DEFAULT_WIDTH; }
    catch { return DEFAULT_WIDTH; }
  });
  const id = useId();
  const width = boundedWidth(preferred, available);
  current.current = width;
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setAvailable(entry.contentRect.width));
    observer.observe(workspace.current);
    return () => observer.disconnect();
  }, []);
  function update(value, save = false) {
    const next = boundedWidth(value, available);
    current.current = next;
    setPreferred(next);
    if (save) { try { localStorage.setItem(WIDTH_KEY, String(next)); } catch { /* Optional view preference. */ } }
  }
  function finish(event) {
    if (!drag.current || event.pointerId !== drag.current.pointerId) return;
    drag.current = null;
    setResizing(false);
    update(current.current, true);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }
  return <div ref={workspace} className={`creative-workspace ${resizing ? 'is-resizing' : ''}`} style={{'--inspector-width': `${width}px`}}>
    {children}
    <div className="inspector-resize-handle" role="separator" tabIndex={0} aria-label="调整节点详情宽度" aria-orientation="vertical"
      aria-controls={id} aria-valuemin={boundedWidth(260,available)} aria-valuemax={boundedWidth(900, available)} aria-valuenow={width}
      aria-valuetext={`${width} 像素`} title="左右拖动调整详情宽度；双击恢复默认宽度"
      onPointerDown={event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.focus();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = {x:event.clientX, width, pointerId:event.pointerId};
        setResizing(true);
      }}
      onPointerMove={event => {
        if (drag.current?.pointerId === event.pointerId) update(drag.current.width + drag.current.x - event.clientX);
      }}
      onPointerUp={finish} onPointerCancel={finish} onLostPointerCapture={finish}
      onDoubleClick={() => update(DEFAULT_WIDTH, true)}
      onKeyDown={event => {
        const target = {ArrowLeft:width + 32, ArrowRight:width - 32, Home:260, End:900}[event.key];
        if (target === undefined) return;
        event.preventDefault(); update(target, true);
      }}><span/></div>
    <div className="creative-inspector-host" id={id}>{inspector}</div>
  </div>;
}
