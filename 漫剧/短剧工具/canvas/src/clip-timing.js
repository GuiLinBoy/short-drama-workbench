const duration = value => typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
export const seconds = value => value == null ? '—' : String(Math.round(value * 100) / 100);

// Measured durations stay bound to the adopted file. A new prompt/result cannot
// borrow a previous result's measurement or imply that a plan is a real clip.
export function clipTiming(record = {}, review = null) {
  const timing = record.timing || {};
  const adopted = timing.adopted;
  const matching = adopted && (review
    ? review.kind === 'video' && review.status === 'approved' && (review.files || []).some(file =>
        file.path === adopted.path && file.sha256 === adopted.sha256 && file.resource?.exists)
    : record.status === 'approved' && record.video?.exists && record.video.name === adopted.path);
  return {requested: duration(review?.request?.params?.duration) ?? duration(record.duration),
    planned: duration(timing.planned), adopted: matching ? duration(adopted.duration) : null};
}

export function timingLines(timing) {
  const lines = [];
  if (timing.adopted != null) lines.push(`已采用 ${seconds(timing.adopted)} 秒`);
  else if (timing.planned != null) lines.push(`计划采用 ${seconds(timing.planned)} 秒`);
  if (timing.requested != null) lines.push(`${timing.adopted != null ? '原生成请求' : '拟生成'} ${seconds(timing.requested)} 秒`);
  return lines;
}
