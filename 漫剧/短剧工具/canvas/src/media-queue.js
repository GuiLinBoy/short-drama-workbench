export function createMediaQueue(limit = 2) {
  const waiting = [];
  let running = 0;
  function pump() {
    while (running < limit && waiting.length) {
      const job = waiting.shift();
      if (job.done) continue;
      running++; job.started = true;
      const cleanup = job.start(job.finish);
      job.cleanup = cleanup;
      // Also release tasks that finish synchronously during startup.
      if (job.done) cleanup?.();
    }
  }
  return start => {
    const job = {start, done:false, started:false, cleanup:null};
    job.finish = () => {
      if (job.done) return;
      job.done = true;
      if (job.started) {running--; job.cleanup?.();}
      else {const index=waiting.indexOf(job); if(index>=0) waiting.splice(index,1);}
      pump();
    };
    waiting.push(job); pump();
    return job.finish;
  };
}
export const scheduleVideoThumbnail = createMediaQueue(2);
