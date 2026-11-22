const CACHE_KEY = 'manju.snapshot.v1';
const MAX_CACHE = 4 * 1024 * 1024;
const MAX_AGE = 24 * 60 * 60 * 1000;
const valid = data => data && Array.isArray(data.projects) && Array.isArray(data.issues)
  && Array.isArray(data.ledger) && typeof data.revision === 'string'
  && data.projects.every(p => p && typeof p.key === 'string'
    && ['assets','clips','tasks','budgets','events','finals','pipeline'].every(key => Array.isArray(p[key])));

function readCache(storage, now) {
  try {
    const raw = storage?.getItem(CACHE_KEY);
    if (!raw || raw.length > MAX_CACHE) return null;
    const value = JSON.parse(raw);
    if (value.version !== 1 || !Number.isFinite(value.savedAt) || value.savedAt > now
      || now - value.savedAt > MAX_AGE || !valid(value.data)) return null;
    return value;
  } catch { return null; }
}

// Cached content is for viewing only. No ETag or review token survives a reload;
// the first network response must validate the current server and its data.
export function createSnapshotClient({storage, fetch: fetchData = globalThis.fetch,
  now = Date.now, timeoutMs = 20000} = {}) {
  const cached = readCache(storage, now());
  let state = {data:cached?.data || null, readAt:cached?.savedAt || null,
    error:'', updating:false, verified:false, reviewToken:''};
  let etag = '', inFlight = null, queued = false, stopped = false, abort = null, cacheTimer;
  const listeners = new Set();
  function publish(change) {
    if (stopped) return;
    state = {...state, ...change};
    listeners.forEach(listener => listener());
  }
  function persist(data, savedAt) {
    clearTimeout(cacheTimer);
    cacheTimer = setTimeout(() => {
      try {
        const raw = JSON.stringify({version:1, savedAt, data:{...data, read_only:true, review_writes:false}});
        if (raw.length <= MAX_CACHE) storage?.setItem(CACHE_KEY, raw);
        else storage?.removeItem(CACHE_KEY);
      } catch { /* Full or disabled storage must not interrupt the live canvas. */ }
    }, 0);
  }
  async function load() {
    publish({updating:true});
    try {
      do {
        queued = false;
        abort = new AbortController();
        const timer = setTimeout(() => abort?.abort(), timeoutMs);
        try {
          const response = await fetchData('/api/snapshot', {headers:etag?{'If-None-Match':etag}:{},
            cache:'no-store', signal:abort.signal});
          if (stopped) return;
          if (response.status === 304) {
            if (!state.verified || !state.data) throw new Error('未读取到当前数据，请重试');
            publish({error:'', readAt:now()});
          } else {
            if (!response.ok) throw new Error('本地服务暂不可用');
            const data = await response.json();
            if (stopped) return;
            if (!valid(data)) throw new Error('本地数据格式异常，请重试');
            etag = response.headers.get('ETag') || '';
            const readAt = now();
            publish({data, error:'', readAt, verified:true,
              reviewToken:response.headers.get('X-Canvas-Review') || ''});
            persist(data, readAt);
          }
        } finally { clearTimeout(timer); abort = null; }
      } while (queued && !stopped);
    } catch (error) {
      etag = '';
      publish({error:error.name==='AbortError'?'读取超时，可重试更新':error.message||'读取失败',
        verified:false, reviewToken:''});
    } finally { publish({updating:false}); }
  }
  return {
    getState:() => state,
    subscribe(listener) {listeners.add(listener); return () => listeners.delete(listener);},
    refresh() {
      if (stopped) return Promise.resolve();
      if (inFlight) {queued = true; return inFlight;}
      inFlight = load().finally(() => {inFlight = null;});
      return inFlight;
    },
    stop() {stopped = true; abort?.abort(); clearTimeout(cacheTimer); listeners.clear();},
  };
}
