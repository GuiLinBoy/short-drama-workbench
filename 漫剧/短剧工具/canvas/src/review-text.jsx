import React, {useEffect, useRef, useState} from 'react';

function TextReader({title, text, close}) {
  const dialog = useRef(null);
  const [fontSize, setFontSize] = useState(18);
  useEffect(() => {
    const element = dialog.current, previousFocus = document.activeElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    element.showModal();
    return () => {
      element.close();
      document.body.style.overflow = overflow;
      if (previousFocus?.isConnected) previousFocus.focus({preventScroll:true});
    };
  }, []);
  return <dialog ref={dialog} className="review-text-reader" aria-label={`${title} · 放大阅读`}
    onCancel={event => {event.preventDefault(); close();}}
    onClick={event => {if (event.target === dialog.current) close();}}>
    <header className="review-reader-header"><h2>{title}</h2><div className="review-reader-controls">
      <button className="secondary-button" disabled={fontSize <= 14} onClick={() => setFontSize(value => Math.max(14,value-2))} aria-label="缩小文字">A−</button>
      <output aria-label="当前字号" aria-live="polite">{fontSize}px</output>
      <button className="secondary-button" disabled={fontSize >= 28} onClick={() => setFontSize(value => Math.min(28,value+2))} aria-label="放大文字">A＋</button>
      <button className="secondary-button" onClick={() => setFontSize(18)}>重置字号</button>
      <button className="primary-button" autoFocus onClick={close} aria-label="关闭放大阅读">关闭</button>
    </div></header>
    <pre className="review-reader-content" style={{fontSize:`${fontSize}px`}} tabIndex={0} aria-label="完整文本">{text}</pre>
    <footer>可选择并复制文字 · Esc 关闭 · 只读查看，不修改送审内容</footer>
  </dialog>;
}

export default function ReviewText({title, text, context}) {
  const [expanded, setExpanded] = useState(false);
  return <>
    <div className="review-text-heading"><h3>{title}</h3>
      <button className="secondary-button" aria-label={`放大阅读${title}`} aria-haspopup="dialog" onClick={() => setExpanded(true)}>放大阅读</button>
    </div>
    <pre className="review-text">{text}</pre>
    {expanded && <TextReader title={context || title} text={text} close={() => setExpanded(false)}/>}
  </>;
}
