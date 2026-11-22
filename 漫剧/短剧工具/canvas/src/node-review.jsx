import React, {useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import ReviewContent from './review-content.jsx';
import {REVIEW_STATES,reviewSelection,selectionIsCurrent,reviewStatusLabel} from './review-model.js';
import {clipTiming} from './clip-timing.js';

const badge = (status,kind) => <span className={`chip ${(REVIEW_STATES[status]||['','red'])[1]}`}>{reviewStatusLabel(status,kind)}</span>;

export default function NodeReview({node,project,token,writable,refresh,selectDependency,footerTarget}) {
  const reviews=node.data.reviewItems || [], active=node.data.activeReview;
  const [selection,setSelection]=useState(()=>reviewSelection(active));
  const [note,setNote]=useState(''),[busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('');
  const transaction=useRef(null);
  const item=(project.reviews?.items||[]).find(value=>value.id===selection.id);
  const current=selectionIsCurrent(selection,item) && selection.id===active?.id && reviews.some(value=>value.id===selection.id);
  const approveLabel={image_prompt:'通过提示词',video_prompt:'通过提示词',image:'通过图片',video:'通过视频',audio:'通过音频',final:'通过成片',storyboard:'通过分镜',plan:'通过方案',exception:'通过申请'}[item?.kind] || '通过当前内容';
  function choose(value) {setSelection(reviewSelection(value));setNote('');setMessage('');setError('');transaction.current=null;}
  async function save(action) {
    if (!current || !writable || busy || action==='approve' && !item.canApprove || action==='resume' && !item.canResume) return;
    if (['request_changes','comment'].includes(action) && !note.trim()) return;
    const decision={action,note,items:[selection]}, bodyKey=JSON.stringify(decision);
    if (transaction.current?.bodyKey!==bodyKey) transaction.current={bodyKey,requestId:crypto.randomUUID()};
    setBusy(true);setMessage('');setError('');
    try {
      const response=await fetch('/api/reviews',{method:'POST',headers:{'Content-Type':'application/json','X-Canvas-Review':token},
        body:JSON.stringify({project:project.key,decision:{...decision,requestId:transaction.current.requestId}})});
      const result=await response.json();
      if (!response.ok) throw new Error(result.error||'审核未保存');
      setMessage('本项决定已保存；没有启动生成。');setNote('');transaction.current=null;
      await refresh();
    } catch(e) {setError(e.message||'连接中断，请核对审核记录，不要重复批准。');await refresh();}
    finally {setBusy(false);}
  }
  const decision=item && (<section className="canvas-node-decision"><label>修改意见 / 补充说明<textarea aria-label="节点审核意见" value={note} maxLength={4000} disabled={busy||!writable} onChange={event=>setNote(event.target.value)} placeholder="要求修改或补充意见时必填"/></label>
        {!writable && <p className="review-error">当前仅可查看，审核写入不可用。</p>}
        {error && <p className="review-error" role="alert">{error}</p>}{message && <p className="review-success" role="status">{message}</p>}
        <div className="review-actions">
          {item.status!=='approved' && <button className="primary-button" disabled={!current||!writable||busy||!item.canApprove} onClick={()=>save('approve')}>{approveLabel}</button>}
          <button className="secondary-button" disabled={!current||!writable||busy||!note.trim()} onClick={()=>save('request_changes')}>要求修改</button>
          <button className="secondary-button" disabled={!current||!writable||busy} onClick={()=>save('defer')}>暂缓</button>
          {item.canResume && <button className="secondary-button" disabled={!current||!writable||busy} onClick={()=>save('resume')}>取消暂缓</button>}
          <button className="text-button" disabled={!current||!writable||busy||!note.trim()} onClick={()=>save('comment')}>仅补充意见</button>
        </div><p className="review-file-path">决定绑定本项具体版本；保存不会启动生成，也不自动批准依赖。</p>
      </section>);
  return <div className="canvas-node-review" aria-label="节点版本审核">
    {!current && <div className="review-blockers" role="alert">内容或审核状态已变化，请重新查看当前版本后再决定。
      <button className="secondary-button" disabled={busy} onClick={()=>choose(active)}>重新读取当前版本</button></div>}
    {item && <>
      <div className="node-review-context"><span>{['video_prompt','image_prompt'].includes(item.kind)?'生成前准备':['image','video','audio','final'].includes(item.kind)?'生成结果':'当前确认'}</span>{badge(item.status,item.kind)}</div>
      {!item.draft && item.problems?.length>0 && <div className="review-blockers"><b>通过前需要处理</b>{item.problems.map((problem,index)=><p key={index}>{problem}</p>)}
        {(item.dependsOn||[]).map(id=>(project.reviews?.items||[]).find(value=>value.id===id)).filter(value=>value && value.status!=='approved')
          .map(value=><button className="text-button" key={value.id} onClick={()=>selectDependency(value.id)}>先查看：{value.title}</button>)}
      </div>}
      <ReviewContent item={item} items={project.reviews?.items||[]} MediaPreview={node.data.ui.MediaPreview} badge={badge} compactMediaTitle timing={node.data.kind==='video'?clipTiming(node.data.record,item):null}/>
      <details className="review-fold"><summary>版本与内容说明</summary><h3>{item.title}</h3><p className="review-description">{item.summary}</p></details>
      {footerTarget ? createPortal(decision,footerTarget) : decision}
    </>}
  </div>;
}
