import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ReactFlow, Background, Panel, MiniMap, Handle, Position, MarkerType, useNodesState } from '@xyflow/react';
import { buildCreativeGraph, applySavedLayout, relatedNodes, nodeKey } from './canvas-model.js';
import CreativeEdge from './creative-edge.jsx';
import { arrangeCanvas, layoutSnapshot } from './canvas-layout.js';
import { useImageViewer } from './image-viewer.jsx';
import ResizableWorkspace from './resizable-workspace.jsx';
import NodeReview from './node-review.jsx';
import ReviewText from './review-text.jsx';
import {REVIEW_STATES,reviewStatusLabel} from './review-model.js';
import {presentCanvasGraph,visibleCanvasEdges} from './canvas-presentation.js';
import VideoThumbnail from './video-thumbnail.jsx';
import {initialCanvasZoom} from './responsive-layout.js';
import {mediaVersion,observeCanvasMedia} from './deferred-media.js';
import {hiddenNodesKey,readHiddenNodes,withoutHiddenNodes} from './hidden-nodes.js';
import {clipTiming,timingLines,seconds} from './clip-timing.js';

const KIND = { project: ['作品设定', 'folder'], asset: ['素材', 'person'], story: ['分镜', 'pen'],
  image: ['图片', 'scene'], video: ['视频', 'film'], audio: ['声音', 'activity'], final: ['成片', 'library'], review:['方案 / 待确认','check'] };
const COLORS = { structure: '#a5b0c1', reference: '#4b84f3', production: '#8897ad', continuity: '#d7952d', assembly: '#8998ad',review:'#9675cd' };
const ASSET = { character: '角色', scene: '场景', prop: '道具' };
const exists = value => Boolean(value?.exists);

function CreativeNode({ data, selected }) {
  const { Icon, Chip, MediaPreview } = data.ui;
  const openImage = useImageViewer();
  const r = data.record;
  const mediaRef = useRef(null);
  const version = mediaVersion(['asset','image'].includes(data.kind) ? data.preview : data.kind==='video' ? exists(data.preview) ? data.preview : r.video : null);
  const [loadedVersion,setLoadedVersion] = useState(null);
  const mediaReady = !version || loadedVersion === version;
  useEffect(() => {
    if (mediaReady || !mediaRef.current) return;
    return observeCanvasMedia(mediaRef.current, () => setLoadedVersion(version));
  }, [version,mediaReady]);
  const canEnlarge = (['asset','image'].includes(data.kind) || data.activeReview?.kind==='image') && exists(data.preview) && !data.placeholder;
  const icon = data.kind === 'asset' ? r.type === 'scene' ? 'scene' : r.type === 'prop' ? 'prop' : 'person' : KIND[data.kind][1];
  const deferredPreview = <div className="node-no-preview"><Icon name={icon} size={30}/><span>移入视野后加载</span></div>;
  return <div ref={mediaRef} className={`creative-node kind-${data.kind} ${['asset','image'].includes(data.kind) && !exists(data.preview) ? 'is-empty' : ''} ${selected ? 'is-selected' : ''}`}>
    <Handle type="target" id="in" position={Position.Left}/>
    <Handle type="target" id="top" position={Position.Top}/>
    <div className="creative-node-head"><Icon name={icon} size={17}/><b>{data.title}</b>
      <details className="node-menu nodrag nopan" onClick={event=>event.stopPropagation()}><summary aria-label={`${data.title}的节点菜单`} title="节点菜单">···</summary>
        <div><button onClick={()=>data.remove(data.nodeId)}>从画布移除</button><small>后台文件与记录保留</small></div></details></div>
    <button className={`creative-node-open nodrag nopan ${canEnlarge ? 'can-enlarge' : ''}`} onClick={event => {
      data.select(data.nodeId);
      if (canEnlarge && (event.target.tagName==='IMG' || event.detail===0)) openImage(data.preview, data.title);
    }} aria-label={`查看${data.title}`} aria-haspopup={canEnlarge ? 'dialog' : undefined}>
      {['asset','image'].includes(data.kind) ? <div className="creative-node-media">{!exists(data.preview) && data.currentPrompt?.request ? <div className="node-story-text"><p>{data.activeReview?.status==='changes_requested'?'等待修改提示词':'当前提示词'}</p><p>{data.currentPrompt.request.prompt.slice(0,160)}</p></div> : mediaReady ? <MediaPreview resource={data.preview} placeholder={data.placeholder} type={r.type} expandable={false}/> : deferredPreview}
        {canEnlarge && <span className="image-preview-hint" aria-hidden="true"><Icon name="search" size={14}/>查看大图</span>}</div> :
        data.kind === 'video' ? <><div className="creative-node-media video-media">{!mediaReady ? deferredPreview : exists(data.preview) ?
          <img src={data.preview.url} alt={data.activeReview?.kind==='image'?'当前开场构图图':'本地定稿尾帧'} decoding="async"/> : exists(r.video) ? <VideoThumbnail resource={r.video} fallback={<div className="node-no-preview"><Icon name="film" size={30}/><span>正在读取视频截图</span></div>}/> : data.activeReview?.kind==='image_prompt' ? <div className="node-story-text"><p>开场构图图 · 待确认提示词</p><p>{data.currentPrompt?.request?.prompt.slice(0,160)}</p></div> : <div className="node-no-preview"><Icon name="film" size={30}/><span>{data.awaitingPrompt ? '旧视频已撤下 · 待准备新版提示词' : data.activeReview?.draft ? '完整提示词草稿 · 待补依赖' : data.awaitingGeneration ? '提示词已准备 · 待生成' : '等待视频素材'}</span></div>}
          {!['image','image_prompt'].includes(data.activeReview?.kind) && <span className="node-duration">{timingLines(clipTiming(r,data.activeReview)).map(line=><span key={line}>{line}</span>)}</span>}</div></> :
        data.kind === 'story' ? <div className="node-story-text">{r.shots?.length ? r.shots.slice(0, 2).map((shot, i) =>
          <p key={i}><span>{shot.start}–{shot.end}s</span>{shot.visual}</p>) : <p>片段已登记，尚无分镜描述。</p>}</div> :
        data.kind === 'project' ? <div className="node-project-text"><span>{r.ratio} · {r.storyboards?.length ? `${r.storyboards.length} 镜 · ${r.clips?.length ? `${r.clips.length} 个视频段` : '视频段落待定'}` : `${r.clips?.length || 0} 个片段`}</span><small>清单中的作品设定</small></div> :
        data.kind === 'review' ? <div className="node-story-text"><p>{data.activeReview?.summary}</p></div> :
        data.kind === 'audio' ? <div className="node-audio-text"><Icon name="activity" size={27}/><p>{r.text || data.activeReview?.title || '已登记音频'}</p><span>{r.duration != null ? `${seconds(r.duration)}s · ` : ''}{exists(r.audio) ? '已有本地音频' : '等待音频素材'}</span></div> :
        <div className="node-final-text"><Icon name="library" size={29}/><p>{data.planned ? '等待片段与后期' : '已有本地成片'}</p><small>{data.planned ? '按本集片段归集 · 尚未生成' : '仍需人工验收'}</small></div>}
    </button>
    {data.kind === 'video' && exists(data.preview) && <button className="creative-node-enlarge nodrag nopan"
      aria-label={`放大查看${data.title}的尾帧`} title="放大查看尾帧" onClick={event => {
        event.stopPropagation(); openImage(data.preview, `${data.title} · 尾帧`);
      }}><Icon name="search" size={15}/></button>}
    {(data.activeReview || data.kind === 'asset' || data.kind === 'video') && <div className="creative-node-foot"><span className="node-content-type">{data.awaitingPrompt ? '待准备视频提示词' : data.activeReview ? data.activeReview.kindLabel || '待确认内容' : data.kind === 'asset' ? ASSET[r.type] || '素材' : '定稿记录'}</span>
      {data.activeReview ? <span className={`node-review-status chip ${(REVIEW_STATES[data.activeReview.status]||['','amber'])[1]}`}>{reviewStatusLabel(data.activeReview.status,data.activeReview.kind)}</span> : <Chip status={r.status}/>}</div>}
    <Handle type="source" id="out" position={Position.Right}/>
    <Handle type="source" id="bottom" position={Position.Bottom}/>
  </div>;
}
const NODE_TYPES = { creative: CreativeNode };
const EDGE_TYPES = { creative: CreativeEdge };

function NodeDetails({ node, graph, select, ui, openReviews, project,token,writable,refresh }) {
  const { Icon, Chip, MediaPreview, TextContent } = ui;
  const [tab, setTab] = useState('content');
  const [footerTarget,setFooterTarget]=useState(null);
  if (!node) return <aside className="creative-inspector"><div className="inspector-welcome"><Icon name="nodes" size={32}/><h2>让关联清晰可见</h2><p>点击画布中的节点，查看素材、提示词与上下游。</p></div></aside>;
  const { kind, title, record: r, planned,activeReview,currentPrompt,reviewItems=[] } = node.data;
  const incoming = graph.edges.filter(edge => edge.target === node.id);
  const outgoing = graph.edges.filter(edge => edge.source === node.id);
  const relations = (edges, upstream) => edges.length ? edges.map(edge => {
    const target = graph.nodes.find(item => item.id === (upstream ? edge.source : edge.target));
    return target && <button key={edge.id} className="relation-row" onClick={() => select(target.id)}><Icon name={KIND[target.data.kind][1]} size={16}/><span><b>{target.data.title}</b><small>{edge.label}</small></span><Icon name="chevron" size={15}/></button>;
  }) : <p className="detail-muted">当前范围内没有关联记录。</p>;
  return <aside className="creative-inspector" aria-label="画布节点详情">
    <div className="creative-inspector-head"><span>节点详情</span><span className="detail-kind">{KIND[kind][0]}</span></div>
    <div className="creative-inspector-title"><Icon name={KIND[kind][1]} size={20}/><h2>{title}</h2></div>
    <div className="node-detail-tabs" aria-label="节点详情视图"><button className={tab === 'content' ? 'selected' : ''} aria-pressed={tab==='content'} onClick={() => setTab('content')}><Icon name="check" size={15}/><span>内容与确认</span></button>
      <button className={tab === 'relations' ? 'selected' : ''} aria-pressed={tab==='relations'} onClick={() => setTab('relations')}><Icon name="nodes" size={15}/><span>关联</span><small className="node-detail-tab-count">{incoming.length + outgoing.length}</small></button></div>
    <div className="creative-inspector-content">
      {tab === 'content' && node.data.awaitingPrompt ? <>
        <p>旧视频已撤下，待准备新版视频提示词。</p>
        <section><h3>构图参考图</h3>{relations(incoming.filter(edge=>graph.nodes.find(value=>value.id===edge.source)?.data.referenceClipId===r.id),true)}</section>
        <p className="detail-muted">{timingLines(clipTiming(r)).join(' · ')}</p>
        <details><summary>历史版本（{node.data.hiddenVersions} 个已收起）</summary><button className="text-button" onClick={()=>openReviews(r.id)}>查看历史内容与审核记录</button></details>
      </> : tab === 'content' && activeReview ? <>
        <NodeReview key={`${node.id}:${activeReview.id}`} node={{...node,data:{...node.data,ui}}} project={project} token={token} writable={writable} refresh={refresh} footerTarget={footerTarget}
          selectDependency={id=>{const target=graph.nodes.find(value=>value.id===graph.reviewNodeById?.[id]);if(target?.data.reviewItems?.some(value=>value.id===id))select(target.id);else openReviews(id);}}/>
        {r.recipe && <details><summary>制作参数（备查）</summary><TextContent resource={r.recipe} copyable/></details>}
        {node.data.hiddenVersions>0 && <details><summary>历史版本（{node.data.hiddenVersions} 个已收起）</summary><button className="text-button" onClick={()=>openReviews(activeReview.subject||activeReview.id)}>查看历史内容与审核记录</button></details>}
      </> : tab === 'relations' ? <>
        <section><h3>输入来源</h3>{relations(incoming, true)}</section><section><h3>下游用途</h3>{relations(outgoing, false)}</section>
      </> : <>
        {['asset','image'].includes(kind) && <><div className="dock-preview"><MediaPreview resource={r.image} placeholder={r.placeholder} type={r.type}/></div>
          <div className="detail-status"><span>{kind==='image'?'图片':ASSET[r.type]} · {r.id}</span><Chip status={r.status}/></div>
          {r.sheet && <section><h3>三视图设定稿</h3><div className="dock-preview"><MediaPreview resource={r.sheet} placeholder={Boolean(r.placeholder && r.sheet.tiny)} type={r.type}/></div></section>}
          {r.detail && <section><h3>资产说明</h3><TextContent resource={r.detail}/></section>}</>}
        {kind === 'video' && <><section><h3>{activeReview ? '当前版本视频' : '定稿视频'}</h3><div className="dock-preview"><MediaPreview resource={r.video} type="video" caption={node.data.awaitingGeneration?'当前新版仅有提示词，尚未生成视频':'当前版本视频尚未产生'}/></div></section>
          {!activeReview && <section><h3>试生成 <Chip status={r.draft?.status || 'planned'}/></h3><div className="dock-preview"><MediaPreview resource={r.draft?.video} type="video" caption="试生成视频尚未产生"/></div></section>}
          <dl className="detail-values"><dt>时长</dt><dd>{r.duration ?? '—'} 秒</dd><dt>所属剧集</dt><dd>{r.episode}</dd><dt>承接来源</dt><dd>{r.continue_from || '独立片段'}</dd></dl></>}
        {kind === 'story' && <><p className="detail-muted">{r.paper ? '剪辑分镜（纸面时长，非生成视频段）' : '来自本地分镜清单'} · {r.duration ?? '—'} 秒</p>{r.shots?.map((shot, i) =>
          <div className="dock-shot" key={i}><span>{shot.start}–{shot.end}s</span><p>{shot.visual}</p>{shot.dialogue && <blockquote>{shot.dialogue}</blockquote>}</div>)}{!r.shots?.length && <p className="detail-muted">尚无分镜描述。</p>}</>}
        {kind === 'project' && <><dl className="detail-values"><dt>作品</dt><dd>{r.title}</dd><dt>画幅</dt><dd>{r.ratio}</dd><dt>资产</dt><dd>{r.assets?.length || 0} 项</dd><dt>剪辑分镜</dt><dd>{r.storyboards?.length || r.clips?.length || 0} 镜</dd><dt>视频段落</dt><dd>{r.clips?.length ? `${r.clips.length} 个已登记` : '待确定'}</dd></dl><p className="detail-muted">此节点展示作品清单中的设定，未补写或推断小说正文。</p></>}
        {kind === 'audio' && <><p className="dock-narration">{r.text}</p><dl className="detail-values"><dt>片段内开始</dt><dd>{r.start ?? 0} 秒</dd><dt>登记时长</dt><dd>{r.duration ?? '—'} 秒</dd></dl><div className="dock-preview"><MediaPreview resource={r.audio} caption="旁白音频尚未产生"/></div></>}
        {kind === 'final' && <><div className="dock-preview"><MediaPreview resource={r.video} type="video" caption="成片视频尚未产生"/></div><p className="detail-muted">{planned ? '按剧集归集片段与旁白，尚无本地成片文件。' : '已有本地成片文件，仍需人工核对画面、声音和字幕。'}</p></>}
        {kind === 'review' && <p className="detail-muted">{activeReview?.summary}</p>}
        {activeReview && <section className="canvas-current-version"><h3>当前送审版本</h3><p>{activeReview.title}</p><p className="detail-muted">{activeReview.summary}</p>
          {node.data.hiddenVersions>0 && <small>画布已收起 {node.data.hiddenVersions} 个历史版本，后台文件及审核记录保留。</small>}
          <button className="text-button" onClick={()=>openReviews(activeReview.subject||activeReview.id)}>查看全部历史审核</button></section>}
        {(currentPrompt || r.prompt) && <section>{currentPrompt ? <ReviewText title="实际提交提示词" text={currentPrompt.request.prompt} context={currentPrompt.title}/> : <TextContent resource={r.prompt} copyable/>}</section>}
        {r.reference_images?.length>0 && <section><h3>参考图</h3>{r.reference_images.map((ref,index)=><div className="dock-preview" key={index}><MediaPreview resource={ref} caption={`参考图 ${index+1}`}/></div>)}</section>}
        {r.recipe && <details><summary>制作参数（备查）</summary><TextContent resource={r.recipe} copyable/></details>}
        <p className="detail-muted">本项尚无送审内容，暂不能确认。</p>
      </>}
    </div>
    {tab==='content' && activeReview && <div className="canvas-review-footer" ref={setFooterTarget} aria-label="固定确认操作"/>}
    <div className="creative-inspector-note"><Icon name="lock" size={13}/>确认仅保存审核结果，不启动生成</div>
  </aside>;
}

export default function CreativeCanvas({ project, projectContext, ui, openReviews,token,writable,refresh,canvasTheme='light' }) {
  const { Icon } = ui;
  const manifestRecords = project.clips;
  const pendingRecords=(project.reviews?.items||[]).filter(item=>['video','video_prompt'].includes(item.kind) && /^EP\d+\//.test(item.subject||''))
    .map(item=>({id:item.subject,episode:item.subject.split('/')[0]}));
  const records=[...new Map([...manifestRecords,...pendingRecords].map(record=>[record.id,record])).values()];
  const reviewEpisodes=(project.reviews?.items||[]).map(item=>/^(EP\d+)(?:\/|$)/.exec(item.subject||'')?.[1]).filter(Boolean);
  const episodes = [...new Set([...records.map(clip => clip.episode),...reviewEpisodes])];
  const [episode, setEpisode] = useState(episodes[0] || 'all');
  const [focus, setFocus] = useState('all');
  const [unused, setUnused] = useState(false), [highlight, setHighlight] = useState(false);
  const [showAllLines,setShowAllLines]=useState(true);

  const [selected, setSelected] = useState(nodeKey('video', project.clips[0]?.id));
  const [message, setMessage] = useState(''), [instance, setInstance] = useState(null);
  const [history, setHistory] = useState([]), [zoom, setZoom] = useState(.85);
  const [hiddenNodes,setHiddenNodes] = useState(() => {
    try {return readHiddenNodes(localStorage, project.key);} catch {return [];}
  });
  const model = useMemo(() => {
    const graph=withoutHiddenNodes(presentCanvasGraph(buildCreativeGraph(project, { episode, focus, unused })),hiddenNodes);
    return {...graph,nodes:arrangeCanvas(graph.nodes,graph.edges)};
  }, [project, episode, focus, unused, hiddenNodes]);
  // Larger cards need wider spacing. Keep old compact layouts under their old key.
  const referenceLayout = Object.keys(project.creative_clip_reference_images || {}).length ? ':separate-images-v1' : '';
  const storageKey = `manju.canvas.layout.v9:${project.key}:${episode}:${focus}:${unused}${referenceLayout}`;
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const scopeRef = useRef('');
  const stageRef=useRef(null);
  const initialViewRef=useRef('');
  useEffect(() => {
    let layout = null;
    try { const saved = localStorage.getItem(storageKey); if (saved && saved.length < 150000) layout = JSON.parse(saved); } catch { /* Layout storage never blocks reading project files. */ }
    const changedScope = scopeRef.current !== storageKey;
    scopeRef.current = storageKey;
    setNodes(previous => applySavedLayout(model.nodes, changedScope ? layout :
      Object.fromEntries(previous.map(node => [node.id, node.position]))));
    setSelected(id => model.nodes.some(node => node.id === id) ? id : model.nodes.find(node => node.data.kind === 'video')?.id || model.nodes[0]?.id || '');
    if (changedScope) { setMessage(''); setHistory([]); }
    // Start at a readable scale instead of shrinking tall asset columns to fit.
    // The explicit “全图” action remains available for the complete overview.
    const frame = requestAnimationFrame(() => {
      if (initialViewRef.current===storageKey || !instance || !stageRef.current) return;
      initialViewRef.current=storageKey;
      const {width,height}=stageRef.current.getBoundingClientRect();
      const mainNodes=model.nodes.filter(node=>['asset','image','video'].includes(node.data.kind));
      const contentWidth=Math.max(1,...mainNodes.map(node=>node.position.x+320));
      const initialZoom=initialCanvasZoom(width,height,contentWidth);
      instance.setViewport({x:20,y:20,zoom:initialZoom},{duration:0});
    });
    return () => cancelAnimationFrame(frame);
  }, [model, storageKey, instance, setNodes]);
  const connected = useMemo(() => relatedNodes(model.edges, selected), [model.edges, selected]);
  const visibleNodes = nodes.map(node => ({ ...node, selected: node.id === selected,
    className: highlight && selected && !connected.has(node.id) ? 'node-unrelated' : '',
    data: { ...node.data, ui, nodeId: node.id, select: setSelected, remove:removeNode } }));
  const edges = visibleCanvasEdges(model.edges,selected,showAllLines).map(edge => ({ ...edge, type: 'creative',
    data: { relation: edge.relation, emphasized: edge.source === selected || edge.target === selected ||
      Boolean(highlight && selected && connected.has(edge.source) && connected.has(edge.target)) },
    markerEnd: ['continuity', 'production'].includes(edge.relation) ?
      { type: MarkerType.ArrowClosed, color: COLORS[edge.relation], width: 12, height: 12 } : undefined,
    style: { stroke: COLORS[edge.relation], strokeWidth: edge.relation === 'continuity' ? 1.8 : 1.35,
      opacity: edge.source===selected || edge.target===selected ? 1 : .18 },
    labelStyle: { fill: '#63718a', fontSize: 11 }, labelBgStyle: { fill: '#ffffff' }, labelBgPadding: [6, 4], labelBgBorderRadius: 4 }));
  function saveLayout() {
    try {
      localStorage.setItem(storageKey, JSON.stringify(Object.fromEntries(nodes.map(node => [node.id, node.position]))));
      setMessage('布局已保存在此浏览器，作品文件未改写');
    } catch { setMessage('浏览器无法保存布局；仍可继续查看和拖拽'); }
  }
  function fitAll() {
    requestAnimationFrame(() => instance?.fitView({ padding: .14, maxZoom: .95, duration: 250 }));
  }
  function rememberLayout() {
    setHistory(previous => [...previous, layoutSnapshot(nodes)].slice(-12));
  }
  function updateHidden(ids) {
    setHiddenNodes(ids);
    try {localStorage.setItem(hiddenNodesKey(project.key),JSON.stringify(ids));}
    catch {setMessage('已更新本次画布显示；浏览器无法保存移除记录');return false;}
    return true;
  }
  function removeNode(id) {
    const node = model.nodes.find(item=>item.id===id);
    if (!node) return;
    if (updateHidden([...new Set([...hiddenNodes,id])])) setMessage(`已从画布移除「${node.data.title}」；可在显示设置中恢复，后台资料保留`);
  }
  function restoreNodes() {
    if (updateHidden([])) setMessage('已恢复从画布移除的节点及原有关联线');
  }
  function arrange() {
    if (!nodes.length) return;
    rememberLayout();
    const measured = nodes.map(node => ({ ...node, measured: instance?.getNode(node.id)?.measured ?? node.measured }));
    setNodes(arrangeCanvas(measured, model.edges));
    setMessage(`已按关联关系整理 ${nodes.length} 个节点；可撤销，保存后刷新仍保留`);
    fitAll();
  }
  function undoLayout() {
    const previous = history[history.length - 1];
    if (!previous) return;
    setNodes(current => applySavedLayout(current, previous));
    setHistory(current => current.slice(0, -1));
    setMessage('已撤销上一步布局调整');
    fitAll();
  }
  function restoreLayout() {
    try {
      const saved = localStorage.getItem(storageKey);
      if (!saved) { setMessage('当前查看范围还没有保存过布局'); return; }
      if (saved.length > 150000) { setMessage('保存的布局过大，未应用；当前布局保留'); return; }
      const restored = applySavedLayout(nodes, JSON.parse(saved));
      if (!restored.some((node, index) => node !== nodes[index])) {
        setMessage('保存的记录中没有当前节点可用的坐标'); return;
      }
      if (restored.every((node, index) => node.position.x === nodes[index].position.x && node.position.y === nodes[index].position.y)) {
        setMessage('当前已是保存的布局'); return;
      }
      rememberLayout();
      setNodes(restored);
      setMessage('已恢复上次保存的布局；仍可撤销');
      fitAll();
    } catch { setMessage('无法读取保存的布局，当前布局保留'); }
  }
  function locateSelected() {
    if (selected) instance?.fitView({ nodes: [{ id: selected }], padding: .5, minZoom: .7, maxZoom: 1.1, duration: 300 });
  }
  function locateReview() {
    const pending=model.nodes.filter(node=>node.data.activeReview && node.data.activeReview.status!=='approved');
    const next=pending[(pending.findIndex(node=>node.id===selected)+1)%pending.length];
    if (!next) return;
    setSelected(next.id);
    instance?.fitView({nodes:[{id:next.id}],padding:.35,minZoom:.7,maxZoom:1,duration:250});
  }
  const focused = model.nodes.find(node => node.id === selected);
  const available = records.filter(clip => episode === 'all' || clip.episode === episode);
  return <div className="creative-page">
    <div className="canvas-command-bar" aria-label="作品与画布范围"><div className="canvas-command-scope">{projectContext}<div className="canvas-scope"><select aria-label="画布剧集" value={episode} onChange={event => { setEpisode(event.target.value); setFocus('all'); }}><option value="all">全部剧集</option>{episodes.map(name => <option key={name}>{name}</option>)}</select>
      <select aria-label="画布分镜或片段范围" value={focus} onChange={event => setFocus(event.target.value)}><option value="all">全部分镜 / 片段</option>{available.map(clip => <option key={clip.id} value={clip.id}>{clip.id.split('/').pop()} 及关联</option>)}</select></div></div><div className="head-actions"><button className="canvas-review-link" onClick={locateReview} disabled={!model.nodes.some(node=>node.data.activeReview && node.data.activeReview.status!=='approved')}><Icon name="check" size={16}/>定位待确认 · {model.nodes.filter(node=>node.data.activeReview && node.data.activeReview.status!=='approved').length}</button><button className="primary-button" onClick={saveLayout}><Icon name="check" size={16}/>保存布局</button></div></div>
    <div className="creative-toolbar" aria-label="画布工具">
      <div className="canvas-options canvas-toolbar-groups">
        <details className="canvas-display-settings"><summary className="secondary-button"><Icon name="sliders" size={15}/>显示设置<Icon name="chevron" size={13}/></summary><div className="canvas-display-menu">
          <span>画布显示</span><label><input type="checkbox" checked={showAllLines} onChange={event=>setShowAllLines(event.target.checked)}/>显示全部连线</label>
          <label><input type="checkbox" checked={highlight} onChange={event=>setHighlight(event.target.checked)}/>突出所选节点关联</label>
          <label><input type="checkbox" checked={unused} onChange={event=>setUnused(event.target.checked)}/>显示未引用资产</label>
          {hiddenNodes.length>0 && <button className="secondary-button" onClick={restoreNodes}>恢复移除的节点 · {hiddenNodes.length}</button>}
        </div></details>
        <div className="canvas-layout-actions"><button className="secondary-button" onClick={undoLayout} disabled={!history.length} title="撤销最近的拖拽、整理或恢复">撤销</button>
          <button className="secondary-button" onClick={restoreLayout} title="恢复上次保存的布局">恢复布局</button><button className="secondary-button arrange-button" onClick={arrange}><Icon name="grid" size={15}/>一键整理</button></div>
        <button className="text-button canvas-project-link" onClick={()=>openReviews('')}>作品资料与审核<Icon name="chevron" size={13}/></button>
      </div></div>
    {model.warnings.length > 0 && <div className="canvas-notice has-warning" role="status">{model.warnings.join('；')}</div>}
    {message && <div className="canvas-notice" role="status">{message}</div>}
    <ResizableWorkspace inspector={<NodeDetails key={focused?.id || 'empty'} node={focused} graph={model} select={setSelected} ui={ui} openReviews={openReviews} project={project} token={token} writable={writable} refresh={refresh}/>}><div ref={stageRef} className={`creative-stage ${canvasTheme==='dark'?'is-dark':''}`}>
      <div className="canvas-stage-label"><Icon name="nodes" size={14}/>{model.nodes.length} 个节点 <span>·</span>显示 {edges.length} / {model.edges.length} 条关联{!showAllLines && <span>仅所选节点</span>}{project.counts?.planned_duration!=null && <span>· 全片计划 {seconds(project.counts.planned_duration)} 秒</span>}</div>
      <ReactFlow nodes={visibleNodes} edges={edges} nodeTypes={NODE_TYPES} edgeTypes={EDGE_TYPES} onNodesChange={onNodesChange} onInit={setInstance}
        onNodeClick={(_, node) => setSelected(node.id)}
        onNodeDragStart={rememberLayout} onMove={(_, viewport) => setZoom(viewport.zoom)}
        defaultViewport={{x:24,y:24,zoom:.85}} nodesFocusable={false} edgesFocusable={false}
        nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null} minZoom={.1} maxZoom={1.6}
        proOptions={{ hideAttribution: true }}>
        <Background color={canvasTheme==='dark'?'#394250':'#d8e1ef'} gap={22} size={1}/>
        <Panel position="bottom-left" className="canvas-navigation nodrag nopan"
          onClick={event => event.stopPropagation()} onDoubleClick={event => event.stopPropagation()}>
          <div className="canvas-zoom-group"><button aria-label="缩小画布" title="缩小" onClick={() => instance?.zoomOut({ duration: 180 })}>−</button>
            <button className="canvas-zoom-value" aria-label="恢复100%缩放" title="恢复 100%" onClick={() => instance?.zoomTo(1, { duration: 180 })}>{Math.round(zoom * 100)}%</button>
            <button aria-label="放大画布" title="放大" onClick={() => instance?.zoomIn({ duration: 180 })}>＋</button></div>
          <button className="canvas-nav-button" onClick={fitAll} title="适应全部节点">全图</button>
          <button className="canvas-nav-button" onClick={locateSelected} disabled={!selected} title="定位所选节点">定位所选</button>
        </Panel>
        <MiniMap pannable zoomable nodeColor={node => node.id === selected ? '#4b84f3' : '#dbe3ef'} maskColor={canvasTheme==='dark'?'#111722bb':'#f3f6fb9c'}/></ReactFlow>
      <div className="creative-legend"><span><i className="reference-line"/>素材引用</span><span><i/>制作关联</span><span><i className="continuity-line"/>尾帧承接</span><small>拖动节点标题调整布局</small></div>
    </div></ResizableWorkspace>
  </div>;
}
