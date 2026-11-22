import {projectCanvasReviews} from './canvas-reviews.js';
import {arrangeCanvas} from './canvas-layout.js';
import {foldCanvasStoryboards} from './canvas-storyboards.js';
const list = value => Array.isArray(value) ? value : [];
const exists = resource => Boolean(resource?.exists);
export const nodeKey = (kind, id) => `${kind}:${id}`;

// This is a projection of existing manifests. It never changes project records.
export function buildCreativeGraph(project, { episode = 'all', focus = 'all', unused = false } = {}) {
  const warnings = [];
  const allClips = list(project.clips);
  const byClip = new Map(allClips.map(clip => [clip.id, clip]));
  const inEpisode = allClips.filter(clip => episode === 'all' || clip.episode === episode);
  const center = inEpisode.find(clip => clip.id === focus);
  const clips = center ? inEpisode.filter(clip => clip.id === center.id ||
    clip.id === center.continue_from || clip.continue_from === center.id) : inEpisode;
  for (const clip of clips) {
    const trail = new Set();
    let current = clip;
    while (current) {
      if (trail.has(current.id)) { warnings.push('片段清单存在循环承接，请在制作工具中核对'); break; }
      trail.add(current.id);
      current = byClip.get(current.continue_from);
    }
  }
  const allStories = list(project.storyboards).filter(story => episode === 'all' || story.episode === episode);
  const paperFocus = allStories.find(story => story.id === focus);
  const stories = paperFocus ? [paperFocus] : center ? [] : allStories;
  const needed = new Set([...clips, ...stories].flatMap(clip => list(clip.assets)));
  const clipImages = project.creative_clip_reference_images || {};
  clips.forEach(clip => { if (clipImages[clip.id]) needed.add(clipImages[clip.id]); });
  const byAsset = new Map(list(project.assets).map(asset => [asset.id, asset]));
  for (const id of needed) {
    const parent = byAsset.get(id)?.source_asset_id;
    if (parent) needed.add(parent);
  }
  for (const id of [...needed]) list(project.creative_asset_references?.[id]).forEach(parent => needed.add(parent));
  const assets = list(project.assets).filter(asset => unused || needed.has(asset.id));
  const nodes = [], edges = [], ids = new Set();
  function node(kind, id, title, record, position, extra = {}) {
    const key = nodeKey(kind, id);
    if (ids.has(key)) { warnings.push(`重复节点：${id}`); return key; }
    ids.add(key);
    nodes.push({ id: key, type: 'creative', position, dragHandle: '.creative-node-head',
      data: { kind, title, record, ...extra } });
    return key;
  }
  function edge(source, target, relation, label, vertical = false) {
    if (!ids.has(source) || !ids.has(target)) return;
    edges.push({ id: `${source}>${target}:${relation}`, source, target, relation, label,
      sourceHandle: vertical ? 'bottom' : 'out', targetHandle: vertical ? 'top' : 'in' });
  }
  const work = node('project', project.key, project.title, project, { x: 25, y: 20 });
  let assetY = 185;
  assets.forEach(asset => {
    node('asset', asset.id, asset.name, asset, { x: 25, y: assetY }, { preview: asset.image, placeholder: asset.placeholder });
    assetY += exists(asset.image) ? 420 : 240;
  });
  stories.forEach((story, index) => node('story', story.id, `剪辑分镜 · ${story.id.split('/').pop()}`, story,
    { x: 465, y: 100 + index * 320 }));
  let audioRow = 0;
  clips.forEach((clip, index) => {
    node('story', clip.id, `分镜 · ${clip.id.split('/').pop()}`, clip, { x: 465, y: 100 + stories.length * 320 + index * 400 });
    node('video', clip.id, `视频 · ${clip.id.split('/').pop()}`, clip, { x: 905, y: 100 + stories.length * 320 + index * 400 },
      { preview: clip.ending_frame, status: clip.status });
    list(clip.narration).forEach((audio, audioIndex) => node('audio', `${clip.id}:${audio.id || audioIndex}`,
      `旁白 · ${audio.id || audioIndex + 1}`, audio, { x: 465, y: 145 + stories.length * 320 + clips.length * 400 + audioRow++ * 270 },
      { clipId: clip.id }));
  });
  const episodes = [...new Set(clips.map(clip => clip.episode))];
  episodes.forEach((name, index) => {
    const final = list(project.finals).find(item => item.episode === name);
    if (!final) return;
    node('final', name, `成片 · ${name}`, final,
      { x: 1345, y: 235 + index * 400 }, { planned: !exists(final?.video) });
  });
  assets.forEach(asset => {
    if (asset.source_asset_id) {
      if (!ids.has(nodeKey('asset', asset.source_asset_id))) warnings.push(`${asset.id} 的形态来源 ${asset.source_asset_id} 尚未登记`);
      edge(nodeKey('asset', asset.source_asset_id), nodeKey('asset', asset.id), 'reference', asset.relation || '衍生形态', true);
    }
  });
  assets.forEach(asset => list(project.creative_asset_references?.[asset.id]).forEach(parent => {
    if (parent !== asset.source_asset_id) edge(nodeKey('asset', parent), nodeKey('asset', asset.id), 'reference', '构图参考', true);
  }));
  stories.forEach(story => {
    const target = nodeKey('story', story.id);
    edge(work, target, 'structure', '已登记剪辑分镜');
    list(story.assets).forEach(asset => edge(nodeKey('asset', asset), target, 'reference', '人物 / 场景 / 构图'));
  });
  clips.forEach(clip => {
    const story = nodeKey('story', clip.id), video = nodeKey('video', clip.id);
    edge(work, story, 'structure', '所属作品');
    list(clip.assets).forEach(assetId => {
      if (!ids.has(nodeKey('asset', assetId))) warnings.push(`${clip.id} 引用的资产 ${assetId} 尚未登记`);
      else edge(nodeKey('asset', assetId), story, 'reference', '素材引用');
    });
    edge(story, video, 'production', '分镜关联');
    if (clipImages[clip.id]) edge(nodeKey('asset', clipImages[clip.id]), video, 'reference', '构图参考');
    if (clip.continue_from) {
      if (!byClip.has(clip.continue_from)) warnings.push(`${clip.id} 的承接来源 ${clip.continue_from} 尚未登记`);
      else if (!ids.has(nodeKey('video', clip.continue_from))) warnings.push(`${clip.id} 的承接来源不在当前范围，切换“全部剧集 / 全部片段”查看`);
      edge(nodeKey('video', clip.continue_from), video, 'continuity', '尾帧承接', true);
    }
    edge(video, nodeKey('final', clip.episode), 'assembly', '片段归集');
    list(clip.narration).forEach((audio, index) => {
      const key = nodeKey('audio', `${clip.id}:${audio.id || index}`);
      edge(story, key, 'production', '旁白记录');
      edge(key, nodeKey('final', clip.episode), 'assembly', '旁白归集');
    });
  });
  const graph=foldCanvasStoryboards(projectCanvasReviews({ nodes, edges, warnings: [...new Set(warnings)], clips, stories, assets }, project, {episode,focus}));
  // Embedded props can reuse a character/composition image without duplicating
  // that image as another canvas card. Independent media/reviews remain visible.
  const internal=new Set(list(project.creative_internal_assets));
  const sharedImage=node=>exists(node.data.record.image) && graph.nodes.some(other=>
    other.id!==node.id && other.data.kind==='asset' && !internal.has(other.data.record.id)
    && exists(other.data.record.image) && node.data.record.image.id
    && node.data.record.image.id===other.data.record.image.id);
  const hidden=new Set(graph.nodes.filter(n=>n.data.kind==='asset' && internal.has(n.data.record.id)
    && (!exists(n.data.record.image) || sharedImage(n)) && !n.data.activeReview).map(n=>n.id));
  graph.nodes=graph.nodes.filter(n=>!hidden.has(n.id));
  graph.edges=graph.edges.filter(e=>!hidden.has(e.source) && !hidden.has(e.target));
  // Independent references keep their own image lane through later video revisions.
  // Projects without this explicit mapping retain the existing combined-node view.
  graph.nodes=graph.nodes.map(node=>{
    const clipId=Object.keys(clipImages).find(id=>node.id===nodeKey('asset',clipImages[id]));
    const legacy=node.data.kind==='video' && ['image_prompt','image'].includes(node.data.activeReview?.kind);
    if (!clipId && !legacy) return node;
    const id=clipId || node.data.record.id;
    const title=node.data.activeReview?.title?.match(/^(.+?构图参考图)(?:\s|·|$)/)?.[1] ||
      (id.endsWith('/Clip00001') ? '开场构图参考图' : '构图参考图');
    return {...node,data:{...node.data,kind:'image',referenceClipId:clipId,
      title:`${title} · ${id.split('/').pop()}`,record:{...node.data.record,image:node.data.preview}}};
  });
  return {...graph,nodes:arrangeCanvas(graph.nodes,graph.edges)};
}

export function relatedNodes(edges, selected) {
  const found = new Set(selected ? [selected] : []);
  // Walk ancestors and descendants separately, without pulling in unrelated siblings.
  for (const direction of ['up', 'down']) {
    const visited = new Set(found.has(selected) ? [selected] : []), queue = [...visited];
    while (queue.length) {
      const id = queue.shift();
      for (const edge of edges) {
        const next = direction === 'up' ? edge.target === id && edge.source : edge.source === id && edge.target;
        if (next && !visited.has(next)) { visited.add(next); found.add(next); queue.push(next); }
      }
    }
  }
  return found;
}

export function applySavedLayout(nodes, layout) {
  return nodes.map(node => {
    const point = layout && Object.hasOwn(layout, node.id) ? layout[node.id] : null;
    return point && Number.isFinite(point.x) && Number.isFinite(point.y) &&
      Math.abs(point.x) < 100000 && Math.abs(point.y) < 100000 ?
      { ...node, position: { x: point.x, y: point.y } } : node;
  });
}

