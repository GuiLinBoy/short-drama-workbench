export const REVIEW_STATES = {
  draft: ['草稿 · 待完成准备', 'muted'],
  pending: ['待你确认', 'amber'], approved: ['你已通过', 'green'],
  changes_requested: ['要求修改', 'red'], deferred: ['已暂缓', 'muted'],
  stale: ['内容已变 · 需重审', 'amber'], blocked: ['上游变化 · 已阻断', 'red'],
};
export function reviewStatusLabel(status,kind) {
  if (status!=='pending') return (REVIEW_STATES[status] || ['待核对'])[0];
  return {image_prompt:'待确认图片提示词',video_prompt:'待确认视频提示词',image:'待确认图片结果',video:'待确认视频结果',
    audio:'待确认音频结果',final:'待确认成片',storyboard:'待确认分镜',plan:'待确认方案与预算',exception:'待确认申请'}[kind] || '待你确认';
}
export const REVIEW_ACTIONS = {approve: '通过', request_changes: '要求修改', defer: '暂缓', resume: '取消暂缓，恢复待确认', comment: '补充意见'};
export const REVIEW_STAGES = ['例外申请', '方案与预算', '分镜与图片提示词', '图片与声音样本', '视频提示词', '逐段视频', '成片'];
export function canSubmitReviewBatch({notifications, note, selectionCount, busy, writable}) {
  return Boolean(writable && !busy && notifications?.canNotify && !note.trim() && selectionCount === 0);
}
export function reviewSelection(item) {
  return {id: item.id, fingerprint: item.fingerprint, head: item.head};
}
export function selectionIsCurrent(selection, item) {
  return Boolean(item && selection.fingerprint === item.fingerprint && selection.head === item.head);
}
export function filterReviewItems(items, {stage='all', state='all', tab='pending', query=''}={}) {
  return items.filter(i => (stage === 'all' || i.stage === Number(stage))
    && (tab !== 'pending' || i.status !== 'approved')
    && (state === 'all' || (state === 'deferred' ? i.canResume : i.status === state))
    && (!query || `${i.title} ${i.subject} ${i.id}`.toLowerCase().includes(query.toLowerCase())));
}
export function defaultReviewSelection(items) {
  return Object.fromEntries(items.filter(i => i.canApprove && i.status === 'pending' && !i.canResume)
    .map(i => [i.id, reviewSelection(i)]));
}
export function reviewActionAvailability(items, selection, visible) {
  const byId = new Map(items.map(i=>[i.id,i]));
  const visibleIds = new Set(visible.map(i=>i.id));
  const changed = selection.some(s=>!selectionIsCurrent(s,byId.get(s.id)));
  const hiddenCount = selection.filter(s=>!visibleIds.has(s.id)).length;
  const canAct = selection.length > 0 && !changed && hiddenCount === 0;
  const approvalBlocked = canAct ? selection.map(s=>byId.get(s.id)).filter(item=>!item.canApprove) : [];
  const approvableSelection = canAct ? selection.filter(s=>byId.get(s.id).canApprove) : [];
  return {changed,hiddenCount,canAct,approvalBlocked,approvableSelection,
    canApprove:canAct && selection.every(s=>byId.get(s.id).canApprove),
    canResume:canAct && selection.every(s=>byId.get(s.id).canResume)};
}
export function reviewCounts(items = []) {
  return {pending: items.filter(i => i.status !== 'approved').length,
    approved: items.filter(i => i.status === 'approved').length,
    changed: items.filter(i => ['stale', 'blocked'].includes(i.status)).length};
}
