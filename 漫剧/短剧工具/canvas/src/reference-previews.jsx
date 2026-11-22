import React from 'react';

const KIND = {image:'参考图',tail:'承接尾帧',audio:'声音参考',video:'参考视频'};

export default function ReferencePreviews({references, MediaPreview}) {
  if (!references.length) return null;
  const playable = ref => Boolean(ref.file?.resource?.url && ref.file.exists !== false && ref.file.resource.exists !== false);
  const ready = references.filter(playable).length;
  return <section className="review-section review-references" aria-label="本段引用素材">
    <h3>引用素材 · {references.length} 项 <small>{ready} 项可查看 · {references.length-ready} 项待补</small></h3>
    <div className="reference-grid">{references.map(ref => {
      const available = playable(ref);
      const status = available ? ref.status === 'candidate' ? '可试听 · 待定音' : ref.kind === 'audio' ? '可试听' : '可查看'
        : ref.status === 'pending' ? '待补' : '文件缺失';
      return <article className={`reference-card ${available?'has-media':'is-pending'} kind-${ref.kind}`} key={ref.id}>
        <div className="reference-card-heading"><h4>{KIND[ref.kind] || '素材'} · {ref.label}</h4>
          <span className={`chip ${available && ref.status !== 'candidate'?'blue':'amber'}`}>{status}</span></div>
        {available ? <div className="review-media"><MediaPreview resource={ref.file.resource} caption={ref.label}/></div>
          : <div className="reference-placeholder">{ref.kind==='audio'?'声音尚未就绪':ref.kind==='tail'?'真实尾帧尚未就绪':'素材尚未就绪'}</div>}
        {ref.source && <p className="reference-source">来源：{ref.source}</p>}
        {ref.note && <p className="reference-note">{ref.note}</p>}
        {ref.file && <details className="review-fold"><summary>文件信息</summary>
          <p className="review-file-path">{ref.file.path}{ref.file.sha256 ? ` · SHA ${ref.file.sha256.slice(0,12)}` : ''}</p>
        </details>}
      </article>;
    })}</div>
  </section>;
}
