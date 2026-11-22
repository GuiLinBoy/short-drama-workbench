const list = value => Array.isArray(value) ? value : [];
const time = item => Date.parse(item.submittedAt) || 0;
const newer = (a,b) => time(a) - time(b) || String(a.id).localeCompare(String(b.id),'en',{numeric:true});

// IDs identify an individual submission; kind + subject identifies its visible lane.
// Separate exception requests never replace each other just because they share a subject.
export function latestCanvasReviews(items = [], subjectOf = item => item.subject) {
  const groups = new Map();
  for (const item of items) {
    const key = item.kind === 'exception' || !item.subject ? `id:${item.id}` : `${item.kind}:${subjectOf(item)}`;
    if (!groups.has(key) || newer(item,groups.get(key)) > 0) groups.set(key,item);
  }
  return [...groups.values()];
}

function activeReview(items, all, subjectOf) {
  const sorted = [...items].sort(newer);
  let active = sorted.at(-1);
  const prompt = sorted.findLast(item => ['image_prompt','video_prompt'].includes(item.kind));
  const result = sorted.findLast(item => ['image','video'].includes(item.kind));
  if (prompt && result) {
    const source = list(result.dependsOn).map(id => all.find(item=>item.id===id))
      .find(item=>item?.kind===prompt.kind && subjectOf(item)===subjectOf(prompt));
    if (time(prompt)>time(result) || source && source.id!==prompt.id) active=prompt;
  }
  return active;
}

// Read-only view over manifests and review packets. Hidden history remains intact.
export function projectCanvasReviews(graph, project, {episode='all',focus='all'} = {}) {
  const assets=new Map(list(project.assets).map(asset=>[asset.id,asset]));
  const clips=new Map(list(project.clips).map(clip=>[clip.id,clip]));
  // Explicit revision aliases only: source_asset_id also describes real variants.
  const aliases=project.creative_asset_aliases || {};
  const clipImages=project.creative_clip_reference_images || {};
  const imageAssets=new Set(Object.values(clipImages));
  function canonical(subject) {
    if (imageAssets.has(subject)) return subject;
    const seen=new Set();let value=subject;
    while (typeof aliases[value]==='string') {
      if (seen.has(value)) return subject;
      seen.add(value);value=aliases[value];
    }
    return assets.has(value)||clips.has(value)?value:subject;
  }
  const subjectOf=item=>['image_prompt','image'].includes(item.kind)
    ? clipImages[item.subject] || canonical(item.subject)
    : item.kind==='storyboard'?canonical(item.subject):item.subject;
  const nodeId=id=>{
    if (!id.startsWith('asset:')) return id;
    const subject=canonical(id.slice(6));
    return `${clips.has(subject)?'video':'asset'}:${subject}`;
  };
  const merged=new Map();
  for (const original of graph.nodes) {
    const id=nodeId(original.id);
    if (merged.has(id) && original.id!==id) continue;
    const record=id!==original.id?(clips.get(id.slice(6))||assets.get(id.slice(6))):original.data.record;
    merged.set(id,{...original,id,data:{...original.data,kind:id.split(':')[0],record,title:id!==original.id?(record.name||`视频 · ${record.id.split('/').pop()}`):original.data.title}});
  }
  const nodes=[...merged.values()], edges=[];
  const seenEdges=new Set();
  for (const edge of graph.edges) {
    const source=nodeId(edge.source),target=nodeId(edge.target),key=`${source}>${target}:${edge.relation}`;
    if (source===target || seenEdges.has(key)) continue;
    seenEdges.add(key);edges.push({...edge,source,target});
  }
  const all=list(project.reviews?.items),latest=latestCanvasReviews(all,subjectOf);
  if (!all.length) return {...graph,nodes,edges,reviewNodeById:{},currentReviewItems:[]};
  const byNode=new Map(nodes.map(node=>[node.id,node]));
  const clipIds=new Set(list(project.clips).map(clip=>clip.id));
  function identity(item) {
    const subject=subjectOf(item) || item.id;
    if (item.kind==='plan') return `project:${project.key}`;
    if (['video_prompt','video'].includes(item.kind)) return `video:${subject}`;
    if (['image_prompt','image'].includes(item.kind) && clips.has(subject)) return `video:${subject}`;
    if (item.kind==='final') return `final:${subject}`;
    if (['image_prompt','image'].includes(item.kind) || item.kind==='storyboard' && assets.has(subject)) return `asset:${subject}`;
    if (item.kind==='storyboard' && clipIds.has(subject)) return `story:${subject}`;
    if (item.kind==='audio') {
      const existing=graph.nodes.find(node=>node.data.kind==='audio' && node.data.record.id===subject);
      return existing?.id || `audio:review:${subject}`;
    }
    return `review:${item.kind}:${subject}${item.kind==='exception'?':'+item.id:''}`;
  }
  const nodeByReview=Object.fromEntries(all.map(item=>[item.id,identity(item)]));
  const scoped=latest.filter(item=>{
    const subject=item.subject || '';
    if (episode!=='all' && /^EP\d+(?:\/|$)/.test(subject) && subject.split('/')[0]!==episode) return false;
    if (focus==='all') return item.kind!=='exception' || item.status!=='approved';
    return subject===focus || byNode.has(identity(item));
  });
  // Include dependency subjects even when they haven't entered assets/clips yet.
  const wanted=new Set(scoped.map(identity));
  const visited=new Set();
  function dependencies(item) {
    if (visited.has(item.id)) return;
    visited.add(item.id);
    for (const id of list(item.dependsOn)) {
      const dep=all.find(value=>value.id===id);
      if (!dep) continue;
      wanted.add(identity(dep)); dependencies(dep);
    }
  }
  scoped.forEach(dependencies);
  // Group only latest lanes; exact older dependencies are labelled on their wires.
  const groups=new Map();
  for (const item of latest) {
    const key=identity(item);
    if (!wanted.has(key)) continue;
    if (!groups.has(key)) groups.set(key,[]);
    groups.get(key).push(item);
  }
  let extra=0;
  for (const [id,items] of groups) {
    let node=byNode.get(id);
    const active=activeReview(items,all,subjectOf);
    if (!node) {
      const kind=id.split(':')[0],subject=subjectOf(active) || active.id;
      const record=assets.get(subject) || {id:subject,name:active.title,type:subject.startsWith('C')?'character':subject.startsWith('P')?'prop':'scene',episode:subject.split('/')[0],narration:[]};
      node={id,type:'creative',position:{x:{asset:25,story:465,video:905,audio:905,final:1345,review:465}[kind]||25,y:185+(extra++)*440},
        dragHandle:'.creative-node-head',data:{kind,title:active.title,record}};
      nodes.push(node);byNode.set(id,node);
    }
    const cutoff=all.find(item=>item.id===project.creative_withdrawn_video_through?.[node.data.record.id]
      && item.subject===node.data.record.id && ['video_prompt','video'].includes(item.kind));
    if (node.data.kind==='video' && cutoff && !items.some(item=>newer(item,cutoff)>0)) {
      // An explicit withdrawal is display state, never an invented review decision.
      // Keep the original records available as history until a newer prompt arrives.
      node.data={...node.data,record:{...node.data.record,video:null,draft:null,ending_frame:null,
        duration:null,prompt:null,recipe:null,status:'planned'},preview:null,status:'planned',
        activeReview:null,currentPrompt:null,reviewItems:[],awaitingPrompt:true,awaitingGeneration:false,
        hiddenVersions:all.filter(item=>identity(item)===id).length};
      continue;
    }
    const record={...node.data.record};
    const isPrompt=['image_prompt','video_prompt'].includes(active.kind);
    const rejected=active.status==='changes_requested';
    const media=list(active.files).find(file=>file.resource?.mime?.startsWith(active.kind==='video'?'video/':active.kind==='audio'?'audio/':'image/'))?.resource;
    const prompt=active.request?active:list(active.dependsOn).map(id=>all.find(item=>item.id===id)).find(item=>item?.request);
    const visibleItems=['asset','video'].includes(node.data.kind)?[active]:items;
    node.data={...node.data,record,reviewItems:[...visibleItems].sort(newer),activeReview:active,currentPrompt:prompt,hiddenVersions:all.filter(item=>identity(item)===id).length-visibleItems.length};
    if (node.data.kind==='asset') {
      record.image=active.kind==='image' && !rejected?media:null;
      node.data.preview=record.image; node.data.placeholder=false;
    } else if (node.data.kind==='video') {
      record.video=active.kind==='video' && !rejected?media:null;
      record.draft=null;
      record.duration=active.request?.params?.duration ?? record.duration;
      // A newer prompt must never inherit an older result/ending frame as its output.
      node.data.preview=rejected?null:active.kind==='image'?media:active.kind==='video'?list(active.files).find(file=>file.resource?.mime?.startsWith('image/') && /尾帧/.test(file.label||file.path))?.resource:null;
    } else if (node.data.kind==='audio') record.audio=active.kind==='audio'?media:null;
    else if (node.data.kind==='final') record.video=active.kind==='final'?list(active.files).find(file=>file.resource?.mime?.startsWith('video/'))?.resource:null;
    node.data.awaitingGeneration=isPrompt;
  }
  const edgeIds=new Set(edges.map(edge=>edge.id));
  function externalDependencies(item,target,seen=new Set()) {
    if (seen.has(item.id)) return [];
    seen.add(item.id);
    return list(item.dependsOn).flatMap(id=>{
      const dep=all.find(value=>value.id===id);
      return dep && nodeByReview[id]===target?externalDependencies(dep,target,seen):[id];
    });
  }
  for (const [target,items] of groups) {
    const active=byNode.get(target).data.activeReview;
    if (!active) continue;
    for (const id of new Set(externalDependencies(active,target))) {
      const source=nodeByReview[id];
      if (!byNode.has(source) || source===target) continue;
      const dep=all.find(item=>item.id===id);
      const visible=byNode.get(source).data.reviewItems?.some(item=>item.id===id);
      const edgeId=`review:${id}>${active.id}`;
      if (edgeIds.has(edgeId)) continue;
      edges.push({id:edgeId,source,target,relation:'review',label:visible?'审核依赖':'历史版本依赖',
        sourceHandle:'out',targetHandle:'in',reviewId:id,historical:!visible,dependencyStatus:dep?.status});
      edgeIds.add(edgeId);
    }
  }
  return {...graph,nodes,edges,reviewNodeById:nodeByReview,currentReviewItems:nodes.flatMap(node=>node.data.reviewItems||[])};
}
