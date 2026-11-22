// A workbench must not broaden a project's data into a cross-project view.
export function projectLedger(data, project) {
  const names = new Set([project.folder]);
  const sameTitle = data.projects.filter(p=>p.title===project.title);
  if (sameTitle.length===1 && !data.projects.some(p=>p.key!==project.key && p.folder===project.title)) names.add(project.title);
  return (data.ledger||[]).filter(entry=>names.has(entry.project));
}
