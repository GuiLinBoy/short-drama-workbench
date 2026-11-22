import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';

const ImageViewerContext = createContext(null);
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

export function useImageViewer() {
  return useContext(ImageViewerContext);
}

export function ImageViewerProvider({ children }) {
  const [image, setImage] = useState(null);
  const open = useCallback((resource, title) => {
    if (resource?.exists && resource.url) setImage({ url: resource.url, title: title || resource.name || '本地图片' });
  }, []);
  return <ImageViewerContext.Provider value={open}>{children}
    {image && <ImageViewer key={image.url} image={image} close={() => setImage(null)}/>}
  </ImageViewerContext.Provider>;
}

function ImageViewer({ image, close }) {
  const dialog = useRef(null), stage = useRef(null), gesture = useRef(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [natural, setNatural] = useState({ width: 0, height: 0 });
  const [zoom, setZoom] = useState(null), [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false), [broken, setBroken] = useState(false);
  const ready = natural.width > 0 && size.width > 0 && !broken;
  const fit = ready ? Math.min(1, Math.max(1, size.width - 48) / natural.width,
    Math.max(1, size.height - 48) / natural.height) : 1;
  const scale = zoom ?? fit;

  useEffect(() => {
    const element = dialog.current, previousFocus = document.activeElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    element.showModal();
    return () => {
      element.close();
      document.body.style.overflow = overflow;
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    };
  }, []);

  useEffect(() => {
    const element = stage.current;
    const measure = () => setSize({ width: element.clientWidth, height: element.clientHeight });
    const observer = new ResizeObserver(measure);
    measure(); observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const changeZoom = useCallback(factor => {
    setZoom(current => clamp((current ?? fit) * factor, .05, 4));
    setPan({ x: 0, y: 0 });
  }, [fit]);

  useEffect(() => {
    const element = stage.current;
    const wheel = event => {
      event.preventDefault(); event.stopPropagation();
      if (ready && event.deltaY) changeZoom(event.deltaY < 0 ? 1.15 : 1 / 1.15);
    };
    element.addEventListener('wheel', wheel, { passive: false });
    return () => element.removeEventListener('wheel', wheel);
  }, [changeZoom, ready]);

  function reset() { setZoom(null); setPan({ x: 0, y: 0 }); }
  function original() { setZoom(1); setPan({ x: 0, y: 0 }); }
  function keyDown(event) {
    if (event.ctrlKey || event.metaKey || !ready) return;
    if (['+', '=', '-', '0', '1'].includes(event.key)) {
      event.preventDefault(); event.stopPropagation();
      if (event.key === '0') reset();
      else if (event.key === '1') original();
      else changeZoom(event.key === '-' ? 1 / 1.25 : 1.25);
    }
  }
  function startDrag(event) {
    if (event.button !== 0 || !ready) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = { id: event.pointerId, x: event.clientX, y: event.clientY, pan };
    setDragging(true);
  }
  function moveDrag(event) {
    const start = gesture.current;
    if (!start || start.id !== event.pointerId) return;
    const maxX = Math.max(0, (natural.width * scale - size.width) / 2 + 24);
    const maxY = Math.max(0, (natural.height * scale - size.height) / 2 + 24);
    setPan({ x: clamp(start.pan.x + event.clientX - start.x, -maxX, maxX),
      y: clamp(start.pan.y + event.clientY - start.y, -maxY, maxY) });
  }
  function endDrag() { gesture.current = null; setDragging(false); }

  return <dialog ref={dialog} className="image-viewer" aria-label="图片大图预览"
    onCancel={event => { event.preventDefault(); close(); }} onKeyDown={keyDown}
    onClick={event => { if (event.target === dialog.current) close(); }}>
    <header className="image-viewer-toolbar">
      <div className="image-viewer-title"><h2>{image.title}</h2>
        <span>{natural.width ? `${natural.width} × ${natural.height} 像素` : '正在读取图片…'}</span></div>
      <div className="image-viewer-controls">
        <button onClick={() => changeZoom(1 / 1.25)} disabled={!ready || scale <= .05} aria-label="缩小图片" title="缩小（−）">−</button>
        <output aria-label="图片缩放比例">{Math.round(scale * 100)}%</output>
        <button onClick={() => changeZoom(1.25)} disabled={!ready || scale >= 4} aria-label="放大图片" title="放大（＋）">＋</button>
        <span className="image-viewer-divider"/>
        <button className={zoom === null ? 'is-active' : ''} onClick={reset} disabled={!ready} title="适应窗口（0）">适应窗口</button>
        <button className={zoom === 1 ? 'is-active' : ''} onClick={original} disabled={!ready} title="原始像素（1）">100%</button>
        <button className="image-viewer-close" autoFocus onClick={close} aria-label="关闭图片预览" title="关闭（Esc）">×</button>
      </div>
    </header>
    <div ref={stage} className={`image-viewer-stage ${dragging ? 'is-dragging' : ''}`} aria-label="图片查看区域"
      onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag}
      onLostPointerCapture={endDrag} onDoubleClick={reset}>
      {broken ? <p className="image-viewer-error" role="alert">本地图片无法打开，请核对文件。</p> :
        <img className="image-viewer-image" src={image.url} alt={image.title} draggable={false}
          onLoad={event => setNatural({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
          onError={() => setBroken(true)} style={{ width: natural.width || undefined, height: natural.height || undefined,
            visibility: ready ? 'visible' : 'hidden', transform: `translate(-50%, -50%) translate(${pan.x}px, ${pan.y}px) scale(${scale})` }}/>
      }
    </div>
    <footer className="image-viewer-footer">滚轮缩放 · 拖动查看 · 双击适应窗口 · Esc 关闭</footer>
  </dialog>;
}
