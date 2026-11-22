const list = value => Array.isArray(value) ? value : [];

// Presentation only: preserve original manifests, review IDs and dependency records.
export function foldCanvasStoryboards(graph) {
  const hidden = new Set(graph.nodes.filter(node => node.data.kind === 'story' ||
    node.data.kind === 'review' && node.data.activeReview?.kind === 'storyboard').map(node => node.id));
  if (!hidden.size) return graph;
  const nodes = graph.nodes.filter(node => !hidden.has(node.id)).map(node => ({...node,data:{...node.data}}));
  const byId = new Map(nodes.map(node => [node.id,node]));
  const outgoing = new Map();
  for (const edge of graph.edges) {
    if (!outgoing.has(edge.source)) outgoing.set(edge.source,[]);
    outgoing.get(edge.source).push(edge);
  }
  function destinations(id, seen = new Set()) {
    if (seen.has(id)) return [];
    seen.add(id);
    return (outgoing.get(id)||[]).flatMap(edge => hidden.has(edge.target) ? destinations(edge.target,seen) : [edge.target]);
  }
  const reviewNodeById = {...graph.reviewNodeById};
  for (const old of graph.nodes.filter(node => hidden.has(node.id))) {
    const exactVideo = byId.get(`video:${old.data.record.id}`);
    const videos = exactVideo ? [exactVideo] : [...new Set(destinations(old.id))].map(id=>byId.get(id)).filter(node=>node?.data.kind==='video');
    const targets = videos.length ? videos : nodes.filter(node=>node.data.kind==='project');
    for (const target of targets) {
      target.data.storyboardRecords = [...list(target.data.storyboardRecords),old.data.record];
      const reviews = new Map([...list(target.data.reviewItems),...list(old.data.reviewItems)].map(item=>[item.id,item]));
      target.data.reviewItems = [...reviews.values()];
      if (!target.data.awaitingPrompt && old.data.activeReview && (!target.data.activeReview ||
        target.data.activeReview.status==='approved' && old.data.activeReview.status!=='approved')) target.data.activeReview=old.data.activeReview;
    }
    for (const [id,nodeId] of Object.entries(reviewNodeById)) {
      if (nodeId===old.id && targets[0]) reviewNodeById[id]=targets[0].id;
    }
  }
  const edges=[], keys=new Set();
  function add(edge) {
    if (edge.source===edge.target || !byId.has(edge.source) || !byId.has(edge.target)) return;
    const key=[edge.source,edge.target,edge.relation,edge.reviewId||''].join('|');
    if (keys.has(key)) return;
    keys.add(key);edges.push(edge);
  }
  for (const edge of graph.edges) {
    if (hidden.has(edge.source)) {
      const video=byId.get(`video:${edge.source.replace(/^story:/,'')}`);
      if (video && !hidden.has(edge.target) && byId.get(edge.target)?.data.kind==='audio') add({...edge,id:`fold:${edge.id}`,source:video.id});
      continue;
    }
    if (!hidden.has(edge.target)) {add(edge);continue;}
    const targets=[...new Set(destinations(edge.target))].filter(id=>byId.get(id)?.data.kind==='video');
    for (const target of targets) {
      add({...edge,id:`fold:${edge.id}>${target}`,target,targetHandle:'in'});
    }
  }
  const reviews=new Map(nodes.flatMap(node=>list(node.data.reviewItems)).map(item=>[item.id,item]));
  return {...graph,nodes,edges,reviewNodeById,currentReviewItems:[...reviews.values()]};
}
