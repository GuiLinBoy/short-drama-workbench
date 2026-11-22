export const hiddenNodesKey = project => `manju.canvas.hidden.v1.${project}`;
export function readHiddenNodes(storage, project) {
  try {
    const raw = storage.getItem(hiddenNodesKey(project));
    if (!raw || raw.length > 150000) return [];
    const ids = JSON.parse(raw);
    return Array.isArray(ids) ? [...new Set(ids.filter(id => typeof id === 'string' && id.length < 300))].slice(0, 2000) : [];
  } catch {return [];}
}
export function withoutHiddenNodes(graph, ids) {
  const hidden = new Set(ids);
  return {...graph, nodes:graph.nodes.filter(node => !hidden.has(node.id)),
    edges:graph.edges.filter(edge => !hidden.has(edge.source) && !hidden.has(edge.target))};
}
