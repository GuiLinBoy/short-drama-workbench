import React from 'react';
import {reviewPresentation} from './review-presentation.js';
import ReviewText from './review-text.jsx';
import PhonePreview from './phone-preview.jsx';
import ReferencePreviews from './reference-previews.jsx';
import {timingLines} from './clip-timing.js';

function FileInfo({file}) {
  return <p className="review-file-path">{file.path} · {file.sha256 ? `SHA ${file.sha256.slice(0,12)}` : '文件缺失'}</p>;
}

export default function ReviewContent({item, items, MediaPreview, badge,compactMediaTitle=false,timing=null}) {
  const view = reviewPresentation(item, items);
  const preparing=['video_prompt','image_prompt'].includes(item.kind);
  const media=(view.media.map((file,i) => <section key={file.path+i} className="review-section">{(preparing || !compactMediaTitle || i>0) && <h3>{preparing?`${file.resource?.mime?.startsWith('audio/')?'参考音频':file.resource?.mime?.startsWith('video/')?'参考视频':'参考图'} · ${file.label || '生成参考素材'}`:file.label}</h3>}
      <div className="review-media"><MediaPreview resource={file.resource} caption={file.label}/></div>
      {file.resource?.mime?.startsWith('video/') && <PhonePreview resource={file.resource} title={item.title}/>}
      <details className="review-fold"><summary>文件与版本信息（备查）</summary><FileInfo file={file}/></details>
    </section>));
  return <>
    {view.draftPrompt && <section className="review-section review-prompt draft-prompt">
      <ReviewText title="本段视频提示词 · 草稿" text={view.draftPrompt.text} context={item.title}/>
    </section>}
    {timing && timingLines(timing).length>0 && <section className="review-timing" aria-label="片段时长">
      {timingLines(timing).map(line=><span key={line}>{line}</span>)}
      {timing.adopted==null && timing.planned!=null && <small>采用时长为剪辑计划，实际以完整台词和动作确定。</small>}
    </section>}
    {item.status==='changes_requested' && <p className="review-preparation-notice">已要求修改，旧图或视频已从当前展示撤下，等待更新提示词。</p>}
    {preparing && <p className="review-preparation-notice">{item.draft ? '当前可查看和提意见，尚不能批准生成；待补素材见下方引用清单。' : `${item.kind==='video_prompt'?'视频尚未生成':'图片尚未生成'}，当前确认的是生成提示词。`}</p>}
    {!preparing && media}
    {view.prompts.map(source => <section className="review-section review-prompt" key={source.id}>
      {source.id !== item.id && <p className="review-prompt-source">{source.title} {badge(source.status,source.kind)}</p>}
      {source.id !== item.id && source.status !== 'approved' && <p className="review-error">关联提示词当前未通过或已变化，请先核对上游版本。这里展示的是当前送审提示词。</p>}
      <ReviewText title={source.id === item.id ? '实际提交提示词' : '对应提示词'} text={source.request.prompt} context={source.title}/>
      <details className="review-fold"><summary>生成参数与请求记录（备查）</summary>
        <dl className="review-params">{Object.entries(source.request.params || {}).map(([k,v]) => <React.Fragment key={k}><dt>{k}</dt><dd>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd></React.Fragment>)}</dl>
        <p className="review-file-path">批次 {source.generation?.batch} · 请求 {source.request.path}<br/>本次生成 {source.generation?.taskDir}</p>
      </details>
    </section>)}
    {preparing && <ReferencePreviews references={view.references} MediaPreview={MediaPreview}/>}
    {['image', 'video'].includes(item.kind) && !view.prompts.length && <section className="review-section"><p className="review-missing-prompt">本项未关联可直接展示的正式提示词，请总导演补充；可在下方上游审核中查看已有材料。</p></section>}
    <section className="review-section review-focus"><h3>{item.draft?'本段草稿准备状态':'本次你只需确认'}</h3>
      <ul>{view.focus.map(text => <li key={text}>{text}</li>)}</ul>
      {item.kind === 'final' && <p className="muted">通过表示同意交付此版本；公开发布另行确认。</p>}
    </section>
    {view.primary.map((file,i) => <section key={file.path+i} className="review-section">
      <ReviewText title={file.label} text={file.text}/>
      <details className="review-fold"><summary>文件与版本信息（备查）</summary><FileInfo file={file}/></details>
    </section>)}
    {view.supporting.length > 0 && <section className="review-section review-supporting"><details>
      <summary>专业说明与技术附件（{view.supporting.length} 份，按需查看）</summary>
      <p className="review-file-path">由总导演及专业岗位负责核对；原文完整保留。影响效果的偏差、新增费用和范围变更须在送审说明中明确列出。</p>
      {view.supporting.map((file,i) => <details className="review-fold" key={file.path+i}><summary>{file.label}</summary>
        <pre className="review-text">{file.text}</pre><FileInfo file={file}/>
      </details>)}
    </details></section>}
    {item.draft && <section className="review-section"><details className="review-fold draft-preparation-details">
      <summary>待补事项与准备详情</summary>
      <ul>{item.draft.pending.map((text,i)=><li key={i}>{text}</li>)}</ul>
      {(item.problems || []).filter(text=>!text.startsWith('草稿尚未形成正式请求')).map((text,i)=><p className="review-error" key={i}>{text}</p>)}
    </details></section>}
  </>;
}
