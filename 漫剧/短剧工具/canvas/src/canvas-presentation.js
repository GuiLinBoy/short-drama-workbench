// Hide work-level bookkeeping and merge parallel wires without changing dependencies.
export function presentCanvasGraph(graph) {
  const nodes=graph.nodes.filter(node=>node.data.kind!=='project');
  const visible=new Set(nodes.map(node=>node.id)),groups=new Map();
  for (const edge of graph.edges) {
    if (!visible.has(edge.source) || !visible.has(edge.target)) continue;
    const key=JSON.stringify([edge.source,edge.target]);
    if (!groups.has(key)) groups.set(key,[]);
    groups.get(key).push(edge);
  }
  const edges=[...groups.values()].map(links=>{
    const main=links.find(edge=>edge.relation==='continuity') || links.find(edge=>edge.relation==='reference') || links[0];
    const historical=links.some(edge=>edge.historical);
    return {...main,links,historical,label:historical?`${main.label} · 含历史版本依赖`:main.label};
  });
  const reviews=new Map(nodes.flatMap(node=>node.data.reviewItems||[]).map(item=>[item.id,item]));
  return {...graph,nodes,edges,currentReviewItems:[...reviews.values()]};
}

export function visibleCanvasEdges(edges, selected, showAll=false) {
  return showAll ? edges : edges.filter(edge=>edge.source===selected || edge.target===selected);
}
