const views = new Map([['canvas','creative'],['assets','assets'],['tasks','tasks'],['reviews','reviews'],
  ['pipeline','pipeline'],['clips','clips'],['timeline','timeline']]);
export const PROJECT_STATES = {in_progress:'制作中',completed:'已完成',archived:'已归档'};
export const projectState = project => Object.hasOwn(PROJECT_STATES, project.library_status) ? project.library_status : 'in_progress';
export function workRoute(view='library', projectKey) {
  const segment = [...views].find(([,name])=>name===view)?.[0];
  return segment && projectKey ? `#/works/${encodeURIComponent(projectKey)}/${segment}` : '#/library';
}
export function parseWorkRoute(hash) {
  if (!hash || hash==='#' || hash==='#/' || hash==='#/library') return {view:'library',projectKey:''};
  const parts = /^#\/works\/([^/]+)\/([^/]+)$/.exec(hash);
  if (parts && views.has(parts[2])) {
    try {
      const projectKey = decodeURIComponent(parts[1]);
      if (/^[A-Za-z0-9_-]{1,120}$/.test(projectKey)) return {view:views.get(parts[2]),projectKey};
    } catch { /* Malformed URLs return to the library. */ }
  }
  return {view:'library',projectKey:'',error:'链接格式无效，请从作品库选择作品。'};
}
