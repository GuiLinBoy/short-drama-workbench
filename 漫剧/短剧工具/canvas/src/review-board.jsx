import React, {useEffect, useMemo, useRef, useState} from 'react';
import ReviewContent from './review-content.jsx';
import {clipTiming} from './clip-timing.js';
import {REVIEW_STATES, REVIEW_ACTIONS, REVIEW_STAGES, reviewSelection, selectionIsCurrent, reviewCounts, canSubmitReviewBatch,
  filterReviewItems, defaultReviewSelection, reviewActionAvailability,reviewStatusLabel} from './review-model.js';

function ReviewBadge({status,kind}) {
  const [name, tone] = REVIEW_STATES[status] || ['状态待核对', 'red'];
  return <span className={`chip ${tone}`}><i/>{reviewStatusLabel(status,kind)}</span>;
}

export default function ReviewBoard({project, token, writable, refresh, ui, initialSubject = ''}) {
  const {Icon, MediaPreview, Empty, date} = ui;
  const board = project.reviews || {items: [], history: []};
  const [tab, setTab] = useState('pending'), [stage, setStage] = useState('all');
  const [stateFilter,setStateFilter]=useState('all');
  const [query, setQuery] = useState(initialSubject), [focused, setFocused] = useState('');
  const [picked, setPicked] = useState({}), [note, setNote] = useState('');
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState('');
  const transaction = useRef(null);
  const notificationTransaction = useRef(null);
  const [notificationMessage,setNotificationMessage]=useState(''),[notificationError,setNotificationError]=useState('');
  const notifications = board.notifications;
  const counts = reviewCounts(board.items);
  const visible = useMemo(() => filterReviewItems(board.items,{stage,state:stateFilter,tab,query}),
    [board.items,stage,stateFilter,tab,query]);
  const item = visible.find(i => i.id === focused) || visible[0];
  const selected = Object.values(picked);
  const {changed,hiddenCount,canAct,canApprove,canResume,approvalBlocked,approvableSelection} = reviewActionAvailability(board.items,selected,visible);
  useEffect(() => {setQuery(initialSubject);}, [initialSubject]);

  function showReview(id) {
    setFocused(id);setStage('all');setStateFilter('all');setQuery('');setTab('all');
  }

  function toggle(value) {
    setFocused(value.id);
    setPicked(old => {
      const next = {...old};
      if (next[value.id] && selectionIsCurrent(next[value.id], value)) delete next[value.id];
      else next[value.id] = reviewSelection(value);
      return next;
    });
    setMessage(''); setError('');
  }
  async function save(action) {
    if (!canAct || busy || !writable || action==='resume' && !canResume || action==='approve' && !canApprove) return;
    const decision = {action, note, items: selected};
    // A retry after a lost response reuses its request ID, not a new approval.
    const bodyKey = JSON.stringify(decision);
    if (transaction.current?.bodyKey !== bodyKey) transaction.current = {bodyKey, requestId: crypto.randomUUID()};
    setBusy(true); setError(''); setMessage('');
    try {
      const response = await fetch('/api/reviews', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Canvas-Review': token},
        body: JSON.stringify({project: project.key, decision: {...decision, requestId: transaction.current.requestId}})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '审核保存失败');
      setMessage(`已保存 ${selected.length} 项「${REVIEW_ACTIONS[action]}」。未启动生成。`);
      setPicked({}); setNote(''); transaction.current = null;
      if (action === 'resume') setStateFilter('pending');
      await refresh();
    } catch (e) {setError(e.message || '连接中断，请刷新核对审核记录。'); await refresh();}
    finally {setBusy(false);}
  }

  async function notifyDirector() {
    if (!canSubmitReviewBatch({notifications, note, selectionCount:selected.length, busy, writable})) return;
    const fingerprint = notifications.batchFingerprint;
    if (notificationTransaction.current?.fingerprint !== fingerprint)
      notificationTransaction.current = {fingerprint, requestId:crypto.randomUUID()};
    setBusy(true);setNotificationError('');setNotificationMessage('');
    try {
      const response = await fetch('/api/review-notifications', {method:'POST',
        headers:{'Content-Type':'application/json','X-Canvas-Review':token},
        body:JSON.stringify({project:project.key,notification:{requestId:notificationTransaction.current.requestId,batchFingerprint:fingerprint}})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '本批通知提交失败');
      setNotificationMessage('本批审核已加入通知队列。投递及导演接收状态见本通知区；制作保持暂停。');
      notificationTransaction.current=null;
      await refresh();
    } catch(e) {setNotificationError(e.message || '连接中断，请刷新核对通知队列。');await refresh();}
    finally {setBusy(false);}
  }

  return <div className="review-board">
    <div className="review-heading"><div><div className="eyebrow">创作审核 / 本地记录</div><h1>待你确认</h1>
      <p>查看具体版本，留下意见，再决定是否通过。保存审核不会启动生成。</p></div>
      <span className="review-paused"><Icon name="lock" size={16}/>生成保持暂停</span></div>
    <div className="review-summary"><div><b>{counts.pending}</b><span>待确认或待修改</span></div><div><b>{counts.approved}</b><span>当前版本已通过</span></div>
      <div><b>{counts.changed}</b><span>内容或上游已变化</span></div><p><Icon name="folder" size={17}/>审核记录保存在本作品目录<br/><small>{board.storage || '协作/审核/记录'}</small></p></div>
    {notifications && <section className="review-notifications" aria-label="本批审核通知">
      <div className="review-notify-heading"><div><h2>审核完一批，再通知总导演</h2>
        <p>已保存但尚未通知：<b>{notifications.unnotifiedDecisions}</b> 条审核记录，涉及 <b>{notifications.unnotifiedItems}</b> 项内容。</p></div>
        <button className="primary-button" disabled={!canSubmitReviewBatch({notifications,note,selectionCount:selected.length,busy,writable})}
          onClick={notifyDirector}><Icon name="arrow" size={16}/>本批审核完成，通知总导演</button></div>
      <p className="review-notify-status"><span className={`live-dot ${notifications.listener.online?'connected':''}`}/>{notifications.listener.label}。通知只要求读取审核，制作仍暂停。</p>
      {(note.trim() || selected.length>0) && <p className="review-notify-warning">请先保存本次决定和修改意见，再提交整批通知；不需要继续处理的勾选可移出本批审核。</p>}
      {notifications.blockedReason && <p className="review-notify-warning">{notifications.blockedReason}</p>}
      {notificationError && <p className="review-error" role="alert">{notificationError}</p>}
      {notificationMessage && <p className="review-success" role="status">{notificationMessage}</p>}
      {notifications.queue.length>0 && <div className="review-notify-queue">{notifications.queue.slice(0,8).map(n=><div key={n.id}>
        <span className={`chip ${n.state==='received'?'green':['delivery_unknown','blocked'].includes(n.state)?'amber':'blue'}`}>{n.label}</span>
        <span>{date(n.time)} · {n.decisionCount} 条审核 / {n.itemCount} 项内容</span><small>通知 {n.id.slice(0,10)}</small>
        {n.detail && <p>{n.detail}</p>}</div>)}</div>}
    </section>}
    <div className="review-toolbar"><div className="tabs">{[['pending','待处理'],['all','全部内容'],['history','审核记录']].map(([key,label]) =>
      <button key={key} disabled={busy} className={tab === key ? 'selected' : ''} onClick={() => setTab(key)}>{label}</button>)}</div>
      {tab !== 'history' && <div className="review-filters"><select value={stage} disabled={busy} onChange={e => setStage(e.target.value)} aria-label="筛选审核环节">
        <option value="all">所有审核环节</option>{REVIEW_STAGES.map((s,i) => <option key={i} value={i}>{i ? `第${i}环节 · ` : ''}{s}</option>)}</select>
        <select value={stateFilter} disabled={busy} onChange={e=>setStateFilter(e.target.value)} aria-label="筛选审核状态"><option value="all">所有审核状态</option>
          {Object.entries(REVIEW_STATES).map(([key,[label]])=><option key={key} value={key}>{label}</option>)}</select>
        <label className="search"><Icon name="search" size={16}/><input value={query} disabled={busy} onChange={e => setQuery(e.target.value)} placeholder="标题、片段或编号" aria-label="搜索审核内容"/></label></div>}</div>
    {board.error && <div className="warning-box" role="alert">{board.error}。审核操作已停止。</div>}
    {!writable && <div className="warning-box">当前服务仅供查看，审核写入未开启。</div>}
    {tab === 'history' ? <div className="review-history-list">{board.history.length ? board.history.map((h,i) => <article key={h.record+h.id+i}>
      <div><span className={`chip ${h.action === 'approve' ? 'green' : 'muted'}`}>{REVIEW_ACTIONS[h.action]}</span><time>{date(h.time)}</time></div>
      <h3>{h.title}</h3><p>{h.note || '未填写附加意见'}</p><small>{h.id} · 版本 {h.fingerprint.slice(0,12)} · {h.source === 'DELEGATED_DIRECTOR_REVIEW' ? '用户授权·总导演代审' : '用户在画布操作'}</small>
    </article>) : <Empty icon="clock" title="还没有用户审核记录">总导演的已有状态不会自动变成你的确认。</Empty>}</div> : !board.items.length ?
      <Empty icon="pen" title="尚无送审材料">总导演将具体提示词、图片、视频及依赖整理为送审包后，会显示在这里。现有作品不会被自动批准或恢复生成。</Empty> :
      <><div className="review-workspace"><section className="review-queue" aria-label="送审内容列表">
        <div className="review-queue-head"><b>{visible.length} 项内容</b><button className="text-button" disabled={busy || !writable}
          onClick={() => setPicked(defaultReviewSelection(visible))}>选择待确认项</button></div>
        {!visible.length && <p className="review-no-results">当前筛选下没有内容。</p>}
        {visible.map(i => <article key={i.id} className={`review-queue-item ${item?.id === i.id ? 'focused' : ''}`}>
          <input type="checkbox" checked={Boolean(picked[i.id])} onChange={() => toggle(i)} disabled={busy || !writable} aria-label={`选择审核 ${i.title}`}/>
          <button className="review-open" onClick={() => {setFocused(i.id);setMessage('');}}><span className="review-kind">{i.kindLabel} · {i.subject || i.id}</span><h3>{i.title}</h3>
            <div><ReviewBadge status={i.status} kind={i.kind}/><small>{i.fingerprint.slice(0,8)}</small></div></button></article>)}
      </section><section className="review-detail" aria-label="送审内容详情">{item && <>
        <header><div className="eyebrow">{REVIEW_STAGES[item.stage]} · 版本 {item.fingerprint.slice(0,12)}</div><h2>{item.title}</h2><ReviewBadge status={item.status} kind={item.kind}/>
          {item.summary && !item.draft && <p className="review-description">{item.summary}</p>}
          <button className="secondary-button" disabled={busy || !writable} onClick={() => toggle(item)}>{picked[item.id] && selectionIsCurrent(picked[item.id], item) ? '移出本批审核' : '加入本批审核'}</button></header>
        {!item.draft && item.problems.length > 0 && <div className="review-blockers"><b>通过前需要处理</b>{item.problems.map((p,i) => <p key={i}>{p}</p>)}
          {item.dependsOn.map(id=>board.items.find(value=>value.id===id)).filter(dep=>dep && dep.status!=='approved').map(dep=>
            <button key={dep.id} className="text-button" disabled={busy} onClick={()=>showReview(dep.id)}>先查看：{dep.title}</button>)}
        </div>}
        <ReviewContent key={item.id + item.fingerprint} item={item} items={board.items} MediaPreview={MediaPreview} badge={(status,kind) => <ReviewBadge status={status} kind={kind}/>} timing={['video','video_prompt'].includes(item.kind)?clipTiming((project.clips||[]).find(clip=>clip.id===item.subject),item):null}/>
        <section className="review-section"><details className="review-fold"><summary>版本、依赖与审核记录（备查）</summary>
        <p className="review-file-path">送审时间 {date(item.submittedAt)} · {item.id}<br/>版本 {item.fingerprint}</p>
        {item.dependsOn.length > 0 && <div><h3>上游审核</h3>{item.dependsOn.map(id => {
          const dep = board.items.find(v => v.id === id); return <button key={id} className="review-dependency" onClick={() => showReview(id)}>
            <span>{dep?.title || id}</span><ReviewBadge status={dep?.status} kind={dep?.kind}/></button>;
        })}</div>}
        <h3>本项审核记录</h3>{item.history.length ? [...item.history].reverse().map((h,i) => <div className="review-inline-history" key={h.record+i}>
          <b>{REVIEW_ACTIONS[h.action]}</b><time>{date(h.time)}</time><p>{h.note || '未填写附加意见'}</p><small>版本 {h.fingerprint.slice(0,12)} · {h.source === 'DELEGATED_DIRECTOR_REVIEW' ? '用户授权·总导演代审' : '用户在画布操作'}</small></div>) : <p className="muted">尚无你的确认或意见。</p>}
        </details></section>
      </>}</section></div>
      <section className="review-decision" aria-label="本批审核操作"><div className="review-selection"><strong>已选 {selected.length} 项</strong>
        <button className="text-button" disabled={busy || !selected.length} onClick={() => setPicked({})}>清空选择</button><span>仅保存审核结果，不触发生成</span></div>
        <label htmlFor="review-note">修改意见 / 补充说明<span>要求修改或补充意见时必填</span></label>
        <textarea id="review-note" value={note} maxLength={4000} onChange={e => setNote(e.target.value)} disabled={busy || !writable} placeholder="写明需要修改的位置、原因与期望效果。意见会随具体版本保存在本地。"/>
        {changed && <p className="review-error" role="alert">所选内容或审核状态已变化。请重新查看，并重新选择对应项目。</p>}
        {hiddenCount>0 && <p className="review-error" role="alert">有 {hiddenCount} 项勾选不在当前筛选范围，已阻止提交。未保存的意见会保留。
          <button className="text-button" disabled={busy} onClick={()=>{setStage('all');setStateFilter('all');setQuery('');setTab('all');setFocused(selected[0]?.id || '');}}>恢复筛选并查看所选</button>，或清空选择。</p>}
        {approvalBlocked.length>0 && <div className="review-approval-help" role="status" aria-live="polite">
          <b>“通过”暂不可用：所选 {selected.length} 项中，有 {approvalBlocked.length} 项尚未满足通过条件。</b>
          <ul>{approvalBlocked.map(blocked=><li key={blocked.id}><button className="text-button" disabled={busy} onClick={()=>showReview(blocked.id)}>{blocked.title}</button>
            <span>{blocked.problems.length ? blocked.problems.join('；') : '请查看本项的阻断说明。'}</span></li>)}</ul>
          <p>请先通过上游材料，再确认依赖它的内容；同时勾选不会自动放行依赖。</p>
          {approvableSelection.length>0 && <button className="secondary-button" disabled={busy || !writable} onClick={()=>{
            setPicked(Object.fromEntries(approvableSelection.map(s=>[s.id,s])));setFocused(approvableSelection[0].id);setMessage('');setError('');
          }}>仅保留可通过的 {approvableSelection.length} 项勾选</button>}
          <small>此操作只调整勾选，不保存批准；其余材料仍保留待审。</small>
        </div>}
        {error && <p className="review-error" role="alert">{error}</p>}{message && <p className="review-success" role="status">{message}</p>}
        <div className="review-actions"><button className="primary-button" disabled={!selected.length || busy || !writable || !canApprove} onClick={() => save('approve')}><Icon name="check" size={16}/>{busy ? '正在保存…' : '通过所选内容'}</button>
          <button className="secondary-button" disabled={!canAct || busy || !writable || !note.trim()} onClick={() => save('request_changes')}>要求修改</button>
          <button className="secondary-button" disabled={!canAct || busy || !writable} onClick={() => save('defer')}>暂缓</button>
          <button className="secondary-button" disabled={!canResume || busy || !writable} onClick={() => save('resume')}>取消暂缓，恢复待确认</button>
          <button className="text-button" disabled={!canAct || busy || !writable || !note.trim()} onClick={() => save('comment')}>仅补充意见</button></div>
        <p className="review-file-path">勾选已暂缓项目后，可以取消暂缓并恢复待确认；不会自动通过、恢复制作或取消已提交的平台任务。</p>
      </section></>}
  </div>;
}
