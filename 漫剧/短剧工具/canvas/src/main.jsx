import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ReactFlow, Background, Controls, Handle, Position, MarkerType } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import './style.css';
import './workspace.css';
import CreativeCanvas from './creative-canvas.jsx';
import ReviewBoard from './review-board.jsx';
import './review.css';
import './phone-preview.css';
import './theme.css';
import './responsive.css';
import {readTheme,applyTheme} from './theme.js';
import useSnapshot from './use-snapshot.js';
import {PROJECT_STATES,projectState,parseWorkRoute,workRoute} from './work-routes.js';
import WorkspaceNavigation,{HomeBrand} from './workspace-navigation.jsx';
import {projectLedger} from './project-data.js';
import './workspace-home.css';
applyTheme(readTheme(localStorage));
import { ImageViewerProvider, useImageViewer } from './image-viewer.jsx';

const NAV = [
  ['creative','创作画布','nodes'],
  ['reviews','待你确认','check'],
  ['library','作品库','library'], ['pipeline','制作流水线','pipeline'], ['assets','资产库','grid'],
  ['clips','镜头依赖图','nodes'], ['tasks','任务与花费','activity'], ['timeline','事件时间线','clock'],
];
const STATE = {planned:['待制作','muted'],generated:['已生成 · 待审','amber'],approved:['已通过','green'],failed:['失败','red'],
  submitted:['已提交','blue'],submitting:['提交占位','blue'],queued:['平台排队','blue'],pending:['等待结果','blue'],
  generating:['生成中','blue'],running:['运行中','blue'],unknown:['结果不明','amber'],succeeded:['生成成功','green'],
  rejected:['已拒绝','red'],cancelled:['已取消','muted'],blocked:['已拦截','red'],invalid:['状态损坏','red']};
const GENRE = {'3d-guoman':'3D 国漫','live-action':'写实真人','urban':'都市漫剧'};
const PROVIDER = {'minimax-h3':'MiniMax H3','compshare-indextts':'IndexTTS','nexorai':'NexorAI','h3':'MiniMax H3','indextts':'IndexTTS'};
const ROLES = [['总导演','统筹 / 审核','director'],['编剧','原文 / 分镜','pen'],['美术','角色 / 场景','palette'],['摄影','视频 / 承接','film'],['剪辑','声音 / 成片','sliders']];
const ASSET_TYPE = {character:'角色',scene:'场景',prop:'道具'};

function Icon({name,size=20,...props}) {
  const shapes={library:<><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M8 4v5m8-5v5M8 13h8m-8 3h5"/></>,
    pipeline:<><path d="M4 6h16M4 18h16M7 6v12m10-12v12"/><circle cx="7" cy="10" r="2"/><circle cx="17" cy="14" r="2"/></>,
    grid:<><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
    nodes:<><rect x="2" y="8" width="6" height="8" rx="1.5"/><rect x="16" y="2" width="6" height="7" rx="1.5"/><rect x="16" y="15" width="6" height="7" rx="1.5"/><path d="M8 12h4V5h4m-4 7v7h4"/></>,
    activity:<><path d="M3 19h18M5 15V9m7 6V4m7 11v-5"/><path d="m3 7 6-3 6 3 6-4"/></>,
    clock:<><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    search:<><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></>,
    arrow:<path d="M4 12h16m-6-6 6 6-6 6"/>, chevron:<path d="m9 5 7 7-7 7"/>, down:<path d="m6 9 6 6 6-6"/>,
    lock:<><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 4v3"/></>,
    refresh:<><path d="M20 8a8.5 8.5 0 0 0-14-3L3 8m0-5v5h5m-4 8a8.5 8.5 0 0 0 14 3l3-3m0 5v-5h-5"/></>,
    info:<><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/></>, close:<path d="m6 6 12 12M6 18 18 6"/>,
    film:<><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18m10-18v18M3 8h4M3 16h4m10-8h4m-4 8h4m-9-7 4 3-4 3z"/></>,
    person:<><circle cx="12" cy="7" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3"/></>, scene:<><rect x="2" y="3" width="20" height="18" rx="2"/><circle cx="16" cy="8" r="2"/><path d="m2 17 6-6 5 5 3-3 6 6"/></>,
    prop:<><path d="m12 2 9 5-9 5-9-5 9-5Zm-9 5v10l9 5 9-5V7M12 12v10"/></>,
    pen:<><path d="m16 3 5 5-12 12-6 1 1-6L16 3Zm-2 2 5 5M4 15l5 5"/></>,
    palette:<><path d="M12 3a9 9 0 1 0 0 18h2a2 2 0 0 0 1-3.7A2 2 0 0 1 16 14h2a3 3 0 0 0 3-3 9 9 0 0 0-9-8Z"/><circle cx="7" cy="10" r="1"/><circle cx="10" cy="6.5" r="1"/><circle cx="15" cy="7" r="1"/></>,
    sliders:<><path d="M5 3v18m7-18v18m7-18v18"/><rect x="2" y="7" width="6" height="4" rx="1"/><rect x="9" y="14" width="6" height="4" rx="1"/><rect x="16" y="5" width="6" height="4" rx="1"/></>,
    director:<><path d="M5 21V3m0 2h13l-3 4 3 4H5"/></>, check:<path d="m5 12 4 4L19 6"/>, play:<path d="m8 4 12 8-12 8V4Z"/>,
    eye:<><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></>,
    folder:<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>,
    alert:<><path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5m0 3v1"/></>, list:<><path d="M8 6h13M8 12h13M8 18h13M3 6h1m-1 6h1m-1 6h1"/></>};
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.55" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{shapes[name] || shapes.info}</svg>;
}
const num=(v,d=0)=>v == null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:d});
const date=v=>!v?'—':new Date(typeof v==='number'?v*1000:v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
const fileExists=r=>Boolean(r?.exists);
function Chip({status,children,tone}) {const state=STATE[status] || [status,'muted'];return <span className={`chip ${tone||state[1]}`}><i/>{children||state[0]||'未记录'}</span>;}
function Empty({icon='film',title,children,compact=false}) {return <div className={`empty ${compact?'compact':''}`}><div className="empty-symbol"><Icon name={icon} size={28}/></div><h3>{title}</h3><p>{children}</p></div>;}
function SectionHead({kicker,title,description,children}) {return <div className="section-head"><div><div className="eyebrow">{kicker}</div><h1>{title}</h1>{description&&<p>{description}</p>}</div><div className="head-actions">{children}</div></div>;}
function MediaPreview({resource,placeholder=false,type='image',caption='',expandable=true}) {
  const [broken,setBroken]=useState(false);
  const openImage=useImageViewer();
  useEffect(()=>setBroken(false),[resource?.url]);
  if(!fileExists(resource)||broken) return <div className="media-empty"><Icon name={type==='video'?'film':type==='character'?'person':type==='scene'?'scene':'prop'} size={40}/><span>{broken?'本地素材无法预览，请核对文件':caption||'素材尚未生成'}</span></div>;
  if(resource.mime?.startsWith('video')) return <video src={resource.url} controls preload="metadata" onError={()=>setBroken(true)} aria-label={caption.replace('尚未产生','预览')||'本地视频预览'}/>;
  if(resource.mime?.startsWith('audio')) return <div className="audio-preview"><Icon name="activity" size={30}/><audio src={resource.url} controls preload="metadata" aria-label={caption||'本地音频试听'} onError={()=>setBroken(true)}/></div>;
  if(placeholder) return <div className="placeholder-media"><div className="crosshair"/><Icon name={type==='character'?'person':type==='scene'?'scene':'prop'} size={56}/><span>占位素材</span><small>{resource.width} × {resource.height} px · 非真实生成</small><img src={resource.url} alt="占位图原始色块" onError={()=>setBroken(true)}/></div>;
  const picture=<img src={resource.url} alt={caption||'本地参考素材'} loading="lazy" decoding="async" onError={()=>setBroken(true)}/>;
  return expandable?<button className="image-preview-trigger nodrag nopan" aria-label={`放大查看${caption||resource.name||'本地图片'}`}
    aria-haspopup="dialog" title="点击查看大图" onClick={event=>{event.stopPropagation();openImage(resource,caption||resource.name);}}>
    {picture}<span className="image-preview-hint" aria-hidden="true"><Icon name="search" size={15}/>查看大图</span></button>:picture;
}


function Dialog({title,subtitle,onClose,children,wide=false}) {
  const ref=useRef(null);
  useEffect(()=>{ref.current?.showModal();return()=>ref.current?.close();},[]);
  return <dialog className={`dialog ${wide?'wide':''}`} ref={ref} onCancel={e=>{e.preventDefault();onClose();}} onClick={e=>{if(e.target===ref.current)onClose();}} aria-label={title}>
    <div className="dialog-head"><div><div className="eyebrow">{subtitle}</div><h2>{title}</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭详情"><Icon name="close"/></button></div>{children}</dialog>;
}
function TextContent({resource,copyable=false}) {
  const [document,setDocument]=useState({id:null,text:'',ready:false}),[message,setMessage]=useState('');
  const version=useRef(0);
  useEffect(()=>{const c=new AbortController();version.current+=1;setMessage('');
    if(!resource?.id){setDocument({id:null,text:'尚无对应的本地文本文件。',ready:false});return;}
    setDocument({id:resource.id,text:'正在读取本地文本…',ready:false});
    fetch('/api/text/'+resource.id,{signal:c.signal}).then(async r=>{const d=await r.json();if(!r.ok)throw new Error(d.error);
      if(!c.signal.aborted)setDocument({id:resource.id,text:d.text||'文件为空',ready:Boolean(d.text)});
    }).catch(e=>{if(e.name!=='AbortError'&&!c.signal.aborted)setDocument({id:resource.id,text:e.message,ready:false});});
    return()=>c.abort();},[resource?.id,resource?.modified,resource?.size]);
  const current=document.id===(resource?.id||null),ready=current&&document.ready;
  const content=current?document.text:'正在读取本地文本…';
  async function copy(){
    if(!ready)return;const requestedVersion=version.current;
    try{await navigator.clipboard.writeText(content);if(version.current===requestedVersion)setMessage('已复制到剪贴板');}
    catch{if(version.current===requestedVersion)setMessage('复制未成功，请选中正文后按 Ctrl+C。');}
  }
  return copyable?<div className="text-document"><div className="text-document-toolbar"><span title={resource?.name}>{resource?.name||'尚未登记文件'}</span>
    <button onClick={copy} disabled={!ready} aria-label="复制当前内容"><Icon name="list" size={14}/>复制内容</button></div>
    <pre className="prompt-text">{content}</pre><p className="text-copy-status" role="status">{message}</p></div>:<pre className="prompt-text">{content}</pre>;
}
function Inspector({item,onClose}) {
  const [tab,setTab]=useState('preview');
  const asset=item.kind==='asset',clip=item.kind==='clip';
  return <Dialog title={asset?item.name:clip?item.id:item.name||'任务详情'} subtitle={asset?'ASSET INSPECTOR':clip?'SHOT INSPECTOR':'TASK RECORD'} onClose={onClose} wide>
    <div className="inspector-bar"><Chip status={item.status}/>{asset&&<span>{ASSET_TYPE[item.type]||item.type} · {item.id}</span>}{clip&&<span>{num(item.duration)} 秒 · {item.episode}</span>}
      <div className="tabs"><button className={tab==='preview'?'selected':''} onClick={()=>setTab('preview')}>预览 / 记录</button>{(asset||clip)&&<button className={tab==='prompt'?'selected':''} onClick={()=>setTab('prompt')}>提示词</button>}{item.recipe&&<button className={tab==='recipe'?'selected':''} onClick={()=>setTab('recipe')}>制作参数</button>}</div></div>
    {tab==='recipe'?<TextContent resource={item.recipe} copyable/>:tab==='prompt'?<><TextContent resource={item.prompt} copyable/>{asset&&item.reference_images?.length>0&&<section className="inspector-references"><h3>生成时使用的参考图</h3>{item.reference_images.map((ref,i)=><div className="small-preview" key={ref?.id||i}><MediaPreview resource={ref} caption={`参考图 ${i+1}`}/></div>)}</section>}</>:asset?<div className="inspector-grid"><div><h4>视频参考图</h4><div className="inspector-media"><MediaPreview resource={item.image} placeholder={item.placeholder} type={item.type}/></div></div><div><h4>三视图设定稿</h4><div className="inspector-media"><MediaPreview resource={item.sheet} placeholder={Boolean(item.placeholder&&item.sheet?.tiny)} type={item.type} caption="尚无三视图文件"/></div></div><div className="inspector-full"><h4>资产说明</h4><TextContent resource={item.detail}/></div></div>:
      clip?<div className="inspector-grid"><div><h4><span className="mono">480P</span> 试生成 <Chip status={item.draft.status}/></h4><div className="inspector-media"><MediaPreview resource={item.draft.video} type="video" caption="试生成视频尚未产生"/></div></div><div><h4><span className="mono">768P</span> 定稿 <Chip status={item.status}/></h4><div className="inspector-media"><MediaPreview resource={item.video} type="video" caption="定稿视频尚未产生"/></div></div>
        <div><h4>真实尾帧</h4><div className="small-preview"><MediaPreview resource={item.ending_frame} caption="尚无定稿尾帧"/></div></div><div><h4>承接关系</h4><p className="data-value mono">{item.continue_from||'独立片段'}</p><p className="muted">{item.continuity_reason||'未记录额外承接说明'}</p><div className="tag-row">{item.assets.map(a=><span key={a} className="tag mono">{a}</span>)}</div></div>
        {item.shots.length>0&&<div className="inspector-full"><h4>分镜记录</h4>{item.shots.map((s,i)=><div key={i} className="shot-line"><span className="mono">{s.start}–{s.end}s</span><div>{s.visual}{s.dialogue&&<p className="dialogue">{s.dialogue}</p>}</div></div>)}</div>}
        {item.narration.length>0&&<div className="inspector-full"><h4>旁白记录</h4>{item.narration.map(n=><div className="narration-line" key={n.id}><p>{n.text}</p><small>片段内 {num(n.start,2)} 秒开始 · 已登记 {num(n.duration,2)} 秒</small>{fileExists(n.audio)&&<audio src={n.audio.url} controls preload="metadata"/>}</div>)}</div>}</div>:
      <div className="task-detail"><dl>{[['平台',PROVIDER[item.provider]||item.provider],['任务 ID',item.task_id||'尚未获得'],['尝试',item.attempt],['本地目录',item.path],['批次',item.batch||'未记录'],['估算积分',num(item.estimate,2)],['实际结算',item.settled?num(item.actual,2):'尚未确认'],['额度状态',item.billing_pending?'待核对':item.refunded?'已退回':item.settled?'已结算':'保持本地预占']].map(([k,v])=><React.Fragment key={k}><dt>{k}</dt><dd>{v}</dd></React.Fragment>)}</dl>{item.error&&<div className="warning-box">{item.error}</div>}<p className="muted">这里只展示本地记录，不会查询、重试或重新提交任务。</p></div>}
  </Dialog>;
}

function Stats({items}) {return <div className="stats-strip">{items.map(([label,value,note])=><div className="stat" key={label}><span>{label}</span><strong>{value}</strong><small>{note}</small></div>)}</div>;}
function ProjectCover({project}) {return <div className="project-cover"><div className="cover-guides"/><div className="cover-corner">LOCAL PROJECT / {project.classification==='sample'?'SAMPLE':'WORKSPACE'}</div><div className="cover-number">{String(project.clips.length).padStart(2,'0')}<span>SHOTS</span></div><div className="cover-title">{project.assets.find(a=>a.type==='scene')?.name||project.title}</div><div className="cover-label"><Icon name="film" size={15}/>{project.ratio}<span>·</span>{project.counts.planned_duration!=null?'成片计划':'生成请求合计'} {num(project.counts.planned_duration??project.counts.duration,2)}s</div><div className="film-perforations">{Array.from({length:18},(_,i)=><i key={i}/>)}</div></div>;}

function LibraryView({data,navigate,token,writable,refresh}) {
  const [query,setQuery]=useState(''),[filter,setFilter]=useState('in_progress'),[classification,setClassification]=useState('all');
  const [busy,setBusy]=useState(''),[message,setMessage]=useState(''),[error,setError]=useState('');
  const projects=data.projects.filter(p=>(filter==='all'||projectState(p)===filter)
    &&(classification==='all'||p.classification===classification)
    &&`${p.title} ${p.genre} ${p.folder}`.toLowerCase().includes(query.toLowerCase()));
  const count=status=>data.projects.filter(p=>projectState(p)===status).length;
  async function changeStatus(project,status) {
    if(!writable||busy||status===projectState(project))return;
    setBusy(project.key);setError('');setMessage('');
    try {
      const response=await fetch('/api/project-status',{method:'POST',headers:{'Content-Type':'application/json','X-Canvas-Review':token},
        body:JSON.stringify({project:project.key,status,expectedStatus:projectState(project)})});
      const result=await response.json();if(!response.ok)throw new Error(result.error||'状态保存失败');
      await refresh();setMessage(`「${project.title}」已标记为${PROJECT_STATES[status]}。`);
    }catch(e){setError(e.message||'状态保存失败，请重试');await refresh();}finally{setBusy('');}
  }
  return <><SectionHead kicker="PROJECT LIBRARY / 作品库" title="作品库" description="选择一部作品，进入它的创作画布。"/>
    <Stats items={[["全部作品",data.projects.length,'本地作品'],['制作中',count('in_progress'),'继续创作'],['已完成',count('completed'),'随时回看'],['已归档',count('archived'),'保留文件，可恢复']]}/>
    <div className="collection-toolbar wrap"><div className="library-status-switch" role="group" aria-label="作品状态筛选">{[...Object.entries(PROJECT_STATES),['all','全部']].map(([status,label])=><button key={status} onClick={()=>setFilter(status)} className={filter===status?'selected':''} aria-pressed={filter===status}>{label} <small>{status==='all'?data.projects.length:count(status)}</small></button>)}</div>
      <div className="filter-group"><select aria-label="筛选作品类型" value={classification} onChange={e=>setClassification(e.target.value)}><option value="all">所有类型</option><option value="unclassified">本地作品</option><option value="sample">样例数据</option><option value="simulation">模拟数据</option></select><label className="search"><Icon name="search" size={17}/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="搜索作品…" aria-label="搜索作品"/></label></div></div>
    {message&&<p className="canvas-notice" role="status">{message}</p>}{error&&<p className="warning-box" role="alert">{error}</p>}
    <div className="library-layout"><div className="project-grid">{projects.map(p=><article className="project-card" key={p.key}>
      <a className="cover-button" href={workRoute('creative',p.key)} aria-label={`打开${p.title}创作画布`}><ProjectCover project={p}/></a>
      <div className="project-body"><div className="card-title-line"><h2>{p.title}</h2><Chip tone={projectState(p)==='completed'?'green':projectState(p)==='archived'?'muted':'blue'}>{PROJECT_STATES[projectState(p)]}</Chip>
        <details className="node-menu project-state-menu"><summary aria-label={`${p.title}的作品菜单`} title="作品菜单">···</summary><div>{Object.entries(PROJECT_STATES).map(([status,label])=><button key={status} disabled={!writable||Boolean(busy)||projectState(p)===status} onClick={e=>{e.currentTarget.closest('details').open=false;changeStatus(p,status);}}>{projectState(p)===status?'当前：':status==='in_progress'?'恢复为':'标记为'}{label}</button>)}</div></details></div>
        <div className="project-meta"><span>{GENRE[p.genre]||p.genre}</span><i/><span>{p.mode==='short'?'独立短片':'连载剧集'}</span><i/><span>{p.ratio}</span>{p.classification==='sample'?<Chip tone="amber">样例数据</Chip>:p.classification==='simulation'?<Chip tone="amber">模拟数据</Chip>:null}</div>
        <div className="project-progress"><div><span>定稿片段</span><span className="mono">{p.counts.clip_approved} / {p.counts.clips}</span></div><div className="progress-track"><i style={{width:`${p.counts.clips?p.counts.clip_approved/p.counts.clips*100:0}%`}}/></div></div>
        <div className="project-bottom"><small>{busy===p.key?'正在保存状态…':`更新于 ${date(p.modified)}`}</small><a className="text-button" href={workRoute('creative',p.key)}>进入画布 <Icon name="arrow" size={17}/></a></div></div></article>)}
      {!projects.length&&<Empty icon="folder" title={query||classification!=='all'?'没有匹配的作品':filter==='all'?'还没有作品':`暂无${PROJECT_STATES[filter]}的作品`}>可切换分类查看其他作品。</Empty>}
    </div><aside className="library-notes"><div className="panel note-panel"><span className="eyebrow">作品管理</span><h3>从这里开始下一段创作</h3><div className="note-rule"><span>01</span><p>通过作品卡片右上角菜单，<br/><b>标记制作状态。</b></p></div><div className="note-rule"><span>02</span><p>已归档作品保留全部资料，<br/><b>可以随时恢复。</b></p></div><div className="note-rule"><span>03</span><p>进入画布后复制浏览器地址，<br/><b>下次可直接打开这部作品。</b></p></div></div></aside></div>
  </>;
}

function PipelineView({project,navigate,inspect}) {
  const present=project.pipeline.filter(s=>s.done===s.total).length;
  const queued=project.assets.filter(a=>a.status==='generated');
  return <><SectionHead kicker="PRODUCTION PIPELINE / 制作流水线" title="制作流水线" description={`${project.title} · 文件与清单驱动的${project.pipeline.length}个阶段；审核结论仍由总导演记录。`}><span className="outline-label mono">{present} / {project.pipeline.length} 文件阶段已就绪</span></SectionHead>
    {project.registry&&<div className="quiet-note"><Icon name="info" size={17}/>作品清单记录：{project.registry.status||'未标记状态'} · 阶段 {project.registry.stage||'未标记'}{project.registry.waiting_on&&` · 等待 ${project.registry.waiting_on}`}</div>}
    <div className="pipeline-scroll"><div className="pipeline-line">{project.pipeline.map((s,i)=><div className={`stage ${s.done===s.total?'present':''} ${i===4?'focus-stage':''}`} key={s.id}><div className="stage-top"><span className="mono">0{i+1}</span><Icon name={s.done===s.total?'check':i===4?'eye':'clock'} size={16}/></div><h3>{s.name}</h3><span className="stage-bot">{s.bot}</span><strong className="stage-count mono">{s.done}<small> / {s.total}</small></strong><p>{s.basis}</p></div>)}</div></div>
    <div className="two-columns"><section className="panel"><div className="panel-head"><div><div className="eyebrow">REVIEW QUEUE</div><h2>需要核对的资产 <small>{queued.length}</small></h2></div><button className="text-button" onClick={()=>navigate('assets')}>全部资产 <Icon name="arrow" size={16}/></button></div>{queued.length?queued.map(a=><button key={a.id} className="review-row" onClick={()=>inspect({...a,kind:'asset'})}><div className="review-thumb"><MediaPreview resource={a.image} placeholder={a.placeholder} type={a.type} expandable={false}/></div><span><b>{a.name}</b><small className="mono">{a.id} {a.placeholder?'· 占位素材':''}</small></span><Chip status={a.status}/><Icon name="chevron" size={17}/></button>):<Empty compact icon="eye" title="暂无待审资产">生成的资产会在这里等待人工核对。</Empty>}</section>
      <section className="panel"><div className="panel-head"><div><div className="eyebrow">TEAM ROLES</div><h2>岗位分工</h2></div><span className="muted micro">非 bot 运行状态</span></div><div className="role-list">{ROLES.map(([name,desc,icon],i)=><div key={name} className="role-row"><div className={`role-icon r${i}`}><Icon name={icon} size={19}/></div><b>{name}</b><span>{desc}</span></div>)}</div><p className="panel-footnote">岗位文档已定义职责；画布不会创建、调度或唤醒 bot。</p></section></div>
    <section className="panel final-panel"><div><span className="eyebrow">FINAL CUT</span><h2>成片与验收</h2><p>完整解码通过之后，还需要看画面、听声音、核对字幕。</p></div>{project.finals.length?project.finals.map((f,i)=><div key={i} className="final-item"><b>{f.episode}</b><Chip tone="amber">待人工验收</Chip>{fileExists(f.video)&&<video src={f.video.url} controls preload="metadata"/>}<small>{num(f.duration,2)} 秒 · {num(f.lufs,2)} LUFS · {f.full_decode||'未记录解码结果'}</small></div>):<div className="final-empty"><Icon name="film" size={34}/><span>还没有成片文件</span></div>}</section>
  </>;
}

function AssetNode({data}) {return <button className={`asset-node nodrag nopan ${data.placeholder?'is-placeholder':''}`} onClick={()=>data.open(data.asset)}><Handle type="target" position={Position.Left}/><Icon name={data.asset.type==='character'?'person':data.asset.type==='scene'?'scene':'prop'} size={22}/><span><small className="mono">{data.asset.id}</small><b>{data.asset.name}</b></span><Chip status={data.asset.status}/><Handle type="source" position={Position.Right}/></button>;}
function AssetsView({project,inspect}) {
  const [type,setType]=useState('all'),[status,setStatus]=useState('all'),[query,setQuery]=useState(''),[mode,setMode]=useState('cards');
  const filtered=project.assets.filter(a=>(type==='all'||a.type===type)&&(status==='all'||a.status===status)&&`${a.id} ${a.name}`.includes(query));
  const open=a=>inspect({...a,kind:'asset'});
  const nodes=filtered.map((a,i)=>({id:a.id,type:'asset',style:{pointerEvents:'all'},position:{x:a.source_asset_id?400:30,y:i*120},data:{asset:a,open,placeholder:a.placeholder}}));
  const ids=new Set(filtered.map(a=>a.id));const edges=filtered.filter(a=>a.source_asset_id&&ids.has(a.source_asset_id)).map(a=>({id:a.id+'-edge',source:a.source_asset_id,target:a.id,markerEnd:{type:MarkerType.ArrowClosed,color:'#4b84f3'},style:{stroke:'#4b84f3'},labelStyle:{fill:'#66758a',fontSize:11},labelBgStyle:{fill:'#ffffff'},label:a.relation||'衍生形态'}));
  return <><SectionHead kicker="ASSET LIBRARY / 资产库" title="资产库" description={`${project.assets.length} 项资产 · 点击查看参考图、三视图和提示词。`}><div className="segmented"><button className={mode==='cards'?'selected':''} onClick={()=>setMode('cards')} aria-label="资产卡片视图"><Icon name="grid" size={17}/></button><button className={mode==='graph'?'selected':''} onClick={()=>setMode('graph')} aria-label="资产形态关系"><Icon name="nodes" size={17}/></button></div></SectionHead>
    <div className="collection-toolbar wrap"><div className="tabs">{[['all','全部'],['character','角色'],['scene','场景'],['prop','道具']].map(([id,title])=><button key={id} className={type===id?'selected':''} onClick={()=>setType(id)}>{title} <small>{id==='all'?project.assets.length:project.assets.filter(a=>a.type===id).length}</small></button>)}</div><div className="filter-group"><select value={status} onChange={e=>setStatus(e.target.value)} aria-label="筛选资产状态"><option value="all">所有状态</option>{['planned','generated','approved','failed'].map(s=><option key={s} value={s}>{STATE[s][0]}</option>)}</select><label className="search"><Icon name="search" size={16}/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="名称或编号" aria-label="搜索资产"/></label></div></div>
    {!filtered.length?<Empty icon="grid" title="没有匹配的资产">尝试调整类型、状态或名称筛选。</Empty>:mode==='graph'?<div className="flow-panel asset-flow"><ReactFlow nodes={nodes} edges={edges} nodeTypes={ASSET_NODES} fitView nodesDraggable={false} nodesFocusable={false} edgesFocusable={false} elementsSelectable={false} nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null} minZoom={.3} maxZoom={1.6} proOptions={{hideAttribution:true}}><Background color="#d7e0ed" gap={24}/><Controls showInteractive={false}/></ReactFlow><div className="flow-legend">形态来源 → 衍生资产 · 只读关系图</div></div>:
      <div className="asset-grid">{filtered.map(a=><button key={a.id} className="asset-card" onClick={()=>open(a)}><div className="asset-card-top"><span className="mono">{a.id}</span><Chip status={a.status}/></div><div className="asset-art"><MediaPreview resource={a.image} placeholder={a.placeholder} type={a.type} expandable={false}/></div><div className="asset-card-bottom"><div><small>{ASSET_TYPE[a.type]||a.type}{a.source_asset_id?' · 衍生形态':''}</small><h3>{a.name}</h3></div><span className="round-arrow"><Icon name="arrow" size={17}/></span></div><div className="asset-foot"><span>{a.image?.exists?`${a.image.width||'—'} × ${a.image.height||'—'} px`:'等待生成'}</span><span>{a.placeholder?'小尺寸占位':a.sheet?.exists?'含三视图':'本地素材'}</span></div></button>)}</div>}
  </>;
}
const ASSET_NODES={asset:AssetNode};
function ClipNode({data}) {const c=data.clip;return <button className="clip-node nodrag nopan" onClick={()=>data.open(c)} aria-label={`查看片段 ${c.id}`}><Handle type="target" position={Position.Left}/><div className="clip-node-head"><b className="mono">{c.id.split('/').pop()}</b><span className="duration">{num(c.duration)}s</span></div><div className="clip-node-art">{fileExists(c.ending_frame)?<img src={c.ending_frame.url} alt="片段尾帧"/>:<><span className="clip-numeral">{c.id.split('Clip').pop()?.replace(/^0+/,'')||'01'}</span><Icon name="film" size={24}/></>}<span className="frame-label">{fileExists(c.video)?'已有本地视频':'视频尚未产生'}</span></div><div className="clip-node-states"><span>试生成<Chip status={c.draft.status}/></span><span>定稿<Chip status={c.status}/></span></div><Handle type="source" position={Position.Right}/></button>;}
const CLIP_NODES={clip:ClipNode};
function graphLayout(clips,open) {
  const byId=new Map(clips.map(c=>[c.id,c])),levels=new Map(),cycle=new Set();
  function depth(c,trail=new Set()){if(levels.has(c.id))return levels.get(c.id);if(trail.has(c.id)){cycle.add(c.id);return 0;}const next=new Set(trail);next.add(c.id);const parent=byId.get(c.continue_from);const d=parent?Math.min(clips.length,depth(parent,next)+1):0;levels.set(c.id,d);return d;}
  clips.forEach(c=>depth(c));const rows=new Map();
  const nodes=clips.map(c=>{const level=levels.get(c.id)||0, row=rows.get(level)||0;rows.set(level,row+1);return {id:c.id,type:'clip',style:{pointerEvents:'all'},position:{x:40+level*350,y:40+row*290},data:{clip:c,open}};});
  const edges=clips.filter(c=>byId.has(c.continue_from)).map(c=>({id:c.id+'-continuity',source:c.continue_from,target:c.id,type:'smoothstep',markerEnd:{type:MarkerType.ArrowClosed,color:'#d7952d'},style:{stroke:'#d7952d',strokeWidth:1.5},label:'承接',labelStyle:{fill:'#66758a',fontSize:11},labelBgStyle:{fill:'#ffffff'}}));
  return {nodes,edges,invalid:cycle.size>0||clips.some(c=>c.continue_from&&!byId.has(c.continue_from))};
}
function ClipsView({project,inspect}) {
  const episodes=[...new Set(project.clips.map(c=>c.episode))], [episode,setEpisode]=useState(episodes[0]||''),[mode,setMode]=useState('graph');
  const clips=project.clips.filter(c=>c.episode===episode),open=c=>inspect({...c,kind:'clip'});const graph=graphLayout(clips,open);
  return <><SectionHead kicker="SHOT MAP / 镜头依赖图" title="镜头依赖图" description="连线表示 continue_from 承接关系。拖动画布、缩放，点击片段对比试生成与定稿。"><select value={episode} onChange={e=>setEpisode(e.target.value)} aria-label="选择剧集">{episodes.map(e=><option key={e}>{e}</option>)}</select></SectionHead>
    <div className="collection-toolbar"><div className="shot-summary"><strong>{clips.length}</strong> 个片段 <i/> <strong>{num(clips.reduce((s,c)=>s+(c.duration||0),0))}</strong> 秒 <i/> {clips.filter(c=>c.status==='approved').length} 个定稿通过</div><div className="tabs"><button className={mode==='graph'?'selected':''} onClick={()=>setMode('graph')}><Icon name="nodes" size={15}/>依赖画布</button><button className={mode==='list'?'selected':''} onClick={()=>setMode('list')}><Icon name="list" size={15}/>片段列表</button></div></div>
    {graph.invalid&&<div className="warning-box"><Icon name="alert" size={17}/>存在缺失的承接引用或循环；此图仅展示记录，请在制作工具中核对。</div>}
    {!clips.length?<Empty title="尚无片段">编剧登记分镜后，这里会自动生成依赖关系。</Empty>:mode==='graph'?<div className="flow-panel"><ReactFlow key={episode} nodes={graph.nodes} edges={graph.edges} nodeTypes={CLIP_NODES} fitView fitViewOptions={{padding:.15,maxZoom:1}} nodesDraggable={false} nodesFocusable={false} edgesFocusable={false} elementsSelectable={false} nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null} minZoom={.25} maxZoom={1.8} proOptions={{hideAttribution:true}}><Background color="#d7e0ed" gap={24}/><Controls showInteractive={false}/></ReactFlow><div className="flow-legend"><span><i className="legend-edge"/>尾帧承接</span><span><i className="legend-gray"/>待制作</span><span><i className="legend-green"/>已通过</span><span className="mono">{episode} / READ ONLY</span></div></div>:<div className="table-wrap"><table><thead><tr><th>片段</th><th>时长</th><th>承接来源</th><th>试生成</th><th>定稿</th><th/></tr></thead><tbody>{clips.map(c=><tr key={c.id}><td className="mono">{c.id}</td><td>{c.duration}s</td><td className="mono">{c.continue_from||'独立'}</td><td><Chip status={c.draft.status}/></td><td><Chip status={c.status}/></td><td><button className="text-button" onClick={()=>open(c)}>查看 <Icon name="arrow" size={15}/></button></td></tr>)}</tbody></table></div>}
    <div className="quiet-note"><Icon name="info" size={17}/>本地视频可以直接播放；不存在的文件会显示为空，不会触发下载或重新生成。</div>
  </>;
}

function TasksView({data,project,inspect}) {
  const [tab,setTab]=useState('tasks'),[status,setStatus]=useState('all');
  const tasks=project.tasks.map(t=>({...t,project:project.title}));
  const ledger=projectLedger(data,project);
  const shown=tasks.filter(t=>status==='all'||status==='active'&&t.active||status==='unknown'&&t.status==='unknown'||status==='ended'&&!t.active);
  const active=tasks.filter(t=>t.active);
  return <><SectionHead kicker="TASKS & COST / 任务与花费" title="任务与花费" description="读取任务状态与本地批次账本；这里不会轮询平台或重新提交。"><span className="outline-label"><Icon name="lock" size={14}/>账户余额 · 未查询</span></SectionHead>
    <Stats items={[["本剧任务占用",num(active.length),'包含 queued / unknown 等状态'],['当前作品积分记账',num(project.counts.points_used,2),'视频 / 旁白 · 含未结算预占'],['当前作品出图占用',`${num(project.counts.images_used)} 张`,'与积分分开记录'],['本剧模拟账单',`${ledger.filter(e=>e.simulated).length} 条`,'独立标注 · 非真实消费']]}/>
    <div className="pool-strip"><span>本剧 H3 / IndexTTS 进行中 <b className="mono">{active.filter(t=>['minimax-h3','compshare-indextts'].includes(t.provider)).length}</b></span><span>本剧 NexorAI 进行中 <b className="mono">{active.filter(t=>t.provider==='nexorai').length}</b></span><small>按本剧本地任务状态统计。</small></div>
    <section className="budget-grid">{project.budgets.map((b,i)=><div key={i} className={`budget-card ${!b.valid||b.pending?'invalid':''}`}><div><b>{b.name}</b><span className="micro">{b.unit==='images'?'图像额度':'积分批次'}</span></div><strong className="mono">{num(b.used,2)}<small> / {num(b.limit,2)} {b.unit==='images'?'张':'积分'}</small></strong><div className="progress-track"><i style={{width:`${b.limit?Math.min(100,(b.used||0)/b.limit*100):0}%`}}/></div><small>{b.pending?`${b.pending} 笔实际费用待核对，不能只看预占值`:!b.valid?'记录有异常，需核对':'按本地预算文件展示，不代表新消费授权'}</small></div>)}</section>
    <div className="collection-toolbar wrap"><div className="tabs"><button className={tab==='tasks'?'selected':''} onClick={()=>setTab('tasks')}>生成任务 <small>{tasks.length}</small></button><button className={tab==='ledger'?'selected':''} onClick={()=>setTab('ledger')}>作品账本 <small>{ledger.length}</small></button></div>{tab==='tasks'&&<div className="filter-group"><select value={status} onChange={e=>setStatus(e.target.value)} aria-label="筛选任务状态"><option value="all">全部状态</option><option value="active">运行 / 占位中</option><option value="unknown">结果不明</option><option value="ended">已结束</option></select></div>}</div>
    {tab==='tasks'?shown.length?<div className="table-wrap"><table><thead><tr><th>任务 / 尝试</th><th>平台</th><th>状态</th><th>等待</th><th>本地用量</th><th/></tr></thead><tbody>{shown.map(t=><tr key={t.id}><td><b>{t.name}</b><small>{t.project} · {t.attempt}</small></td><td>{PROVIDER[t.provider]||t.provider}</td><td><Chip status={t.status}/>{t.billing_pending&&<small className="amber-text">计费待核对</small>}</td><td className={t.over_60min?'amber-text':''}>{t.minutes==null?'—':`${num(t.minutes)} 分钟`}</td><td>{t.images!=null?`${t.images} 张`:t.settled?`${num(t.actual,2)} 积分`:t.estimate!=null?`预占 ${num(t.estimate,2)}`:'未记录'}</td><td><button className="text-button" onClick={()=>inspect({...t,kind:'task'})}>详情 <Icon name="arrow" size={15}/></button></td></tr>)}</tbody></table></div>:<Empty icon="activity" title="当前没有生成任务">画布不会启动生成。制作工具提交后，任务状态会自动出现在这里。</Empty>:
      <><div className="quiet-note amber-note"><Icon name="info" size={17}/>charge、settle 与 refund 是记账事件，不能直接相加作为消费总额；模拟与未归属记录不计入当前作品预算。</div>{ledger.length?<div className="table-wrap"><table><thead><tr><th>时间</th><th>作品 / 来源</th><th>事件</th><th>平台</th><th>记录用量</th><th>数据性质</th></tr></thead><tbody>{ledger.map(e=><tr key={e.id}><td className="mono">{date(e.time)}</td><td><b>{e.project||'未标记作品'}</b><small>{e.batch||'未标记批次'}</small></td><td className="mono">{e.event}</td><td>{PROVIDER[e.provider]||e.provider}</td><td>{e.images!=null?`${e.images} 张`:e.points!=null?`${num(e.points,2)} 积分`:'未记录'}</td><td><Chip tone={e.simulated?'amber':'muted'}>{e.simulated?'历史模拟':e.orphan?'未归属历史':'本地记录'}</Chip></td></tr>)}</tbody></table></div>:<Empty title="暂无账本事件">不会为展示效果生成模拟账单或改写现有账本。</Empty>}</>}
  </>;
}

function TimelineView({data,project}) {
  const [role,setRole]=useState('all');
  const events=project.events.map(e=>({...e,project:project.title})).sort((a,b)=>b.epoch-a.epoch);
  const roles=[...new Set(events.map(e=>e.bot))];const filtered=events.filter(e=>role==='all'||e.bot===role);
  return <><SectionHead kicker="ACTIVITY LOG / 事件时间线" title="事件时间线" description="合并本地事件日志，保留来源。未记录的动作不会由画布猜测或补写。"></SectionHead><div className="collection-toolbar"><div className="tabs"><button className={role==='all'?'selected':''} onClick={()=>setRole('all')}>全部岗位 <small>{events.length}</small></button>{roles.map(r=><button key={r} className={role===r?'selected':''} onClick={()=>setRole(r)}>{r}</button>)}</div><span className="micro muted">每部作品最多展示最近 300 条</span></div>
    {!filtered.length?<Empty icon="clock" title="还没有制作事件记录">bot 或制作工具在 logs/*.jsonl 留下记录后，这条时间线会开始生长。</Empty>:<div className="timeline">{filtered.map(e=><article key={e.id} className="timeline-item"><div className="timeline-time"><span>{date(e.time)}</span><small>{e.project}</small></div><div className="timeline-dot"><Icon name={ROLES.find(r=>r[0]===e.bot)?.[2]||'activity'} size={17}/></div><div className="timeline-content"><div><b>{e.bot}</b><span className="tag">{e.event}</span>{e.stage&&<span className="muted micro">{e.stage}</span>}</div>{e.target&&<h3>{e.target}</h3>}{e.note&&<p>{e.note}</p>}<small className="mono">{e.source}</small></div></article>)}</div>}
  </>;
}

const CANVAS_UI = { Icon, Chip, MediaPreview, TextContent, Empty, date };

function App() {
  const [theme,setTheme]=useState(()=>readTheme(localStorage));
  useEffect(()=>{applyTheme(theme);},[theme]);
  const {data,error,live,readAt,refresh,reviewToken,updating,verified}=useSnapshot();
  const [reviewSubject,setReviewSubject]=useState('');
  const [route,setRoute]=useState(()=>parseWorkRoute(window.location.hash));
  const routeRef=useRef(route);
  const [routeMessage,setRouteMessage]=useState(()=>route.error||'');
  const [inspected,setInspected]=useState(null),[showIssues,setShowIssues]=useState(false);
  const {view,projectKey}=route;
  const project=data?.projects.find(p=>p.key===projectKey);
  useEffect(()=>{
    const changed=()=>{const next=parseWorkRoute(window.location.hash);if(next.view===routeRef.current.view&&next.projectKey===routeRef.current.projectKey&&next.error===routeRef.current.error)return;routeRef.current=next;setRoute(next);setRouteMessage(next.error||'');setReviewSubject('');setInspected(null);window.scrollTo({top:0,behavior:'instant'});};
    window.addEventListener('hashchange',changed);return()=>window.removeEventListener('hashchange',changed);
  },[]);
  function navigate(nextView,key=projectKey,replace=false) {
    const hash=workRoute(nextView,key);
    routeRef.current=parseWorkRoute(hash);setRouteMessage('');
    if(replace)window.history.replaceState(null,'',hash);
    else if(window.location.hash!==hash)window.location.hash=hash;
    setRoute(parseWorkRoute(hash));setInspected(null);setReviewSubject('');
    window.scrollTo({top:0,behavior:'instant'});
  }
  const openReviews=subject=>{navigate('reviews');setReviewSubject(subject||'');};
  useEffect(()=>{
    if(verified&&data&&projectKey&&!project){navigate('library','',true);setRouteMessage('链接对应的作品不存在或已移走，请重新选择作品。');}
  },[verified,data,projectKey,project]);
  const projectContext=project&&<div className="project-context" title={`${GENRE[project.genre]||project.genre} · ${project.ratio}`}><Icon name="folder" size={16}/><select value={project.key} onChange={e=>{navigate(view,e.target.value);}} aria-label="选择作品">{data.projects.map(p=><option key={p.key} value={p.key}>{p.title}</option>)}</select><span className="context-rule"/><span>{GENRE[project.genre]||project.genre} · {project.ratio}</span>{['sample','simulation'].includes(project.classification)&&<span className="sample-badge">{project.classification==='sample'?'样例 / 含占位素材':'模拟数据'}</span>}</div>;
  const issueCount=data?.issues.length||0;
  const isWorkspace=view!=='library'&&Boolean(project);
  return <div className={`app-shell ${isWorkspace?'project-workspace':'library-home'}`}>
    {isWorkspace&&<WorkspaceNavigation project={project} view={view} live={live} Icon={Icon}/>}

    <div className={`main-shell ${isWorkspace&&view==='creative'?'is-creative':''}`}><header className="topbar">{isWorkspace?<div className="breadcrumb"><a href="#/library">返回作品库</a><Icon name="chevron" size={13}/><span className="breadcrumb-project" title={project.title}>{project.title}</span><Icon name="chevron" size={13}/><b>{NAV.find(n=>n[0]===view)?.[1]}</b></div>:<HomeBrand Icon={Icon}/>}<div className="topbar-actions"><label className="studio-theme-switch"><span>外观</span><select aria-label="工作台外观" value={theme} onChange={event=>setTheme(event.target.value)}><option value="light">日间模式</option><option value="dark">夜间模式</option></select></label><span className={`connection ${live?'connected':''}`}><span className="live-dot"/>{updating?(data?'后台更新中':'正在读取数据'):data&&!verified?'显示上次内容':live?'本地实时连接':'连接中 / 待重连'}</span><button className="icon-button" onClick={refresh} aria-label="刷新本地文件" title="刷新本地文件"><Icon name="refresh" size={17}/></button><button className="issues-button" onClick={()=>setShowIssues(true)} aria-label={`查看${issueCount}条数据提示`}><Icon name="info" size={17}/><span>{issueCount}</span></button></div></header>
      <main>{routeMessage&&<div className="canvas-notice" role="status">{routeMessage}<button className="text-button" onClick={()=>setRouteMessage('')}>关闭</button></div>}{error&&<div className="warning-box"><Icon name="alert" size={17}/>{error}。{data?'已保留上次内容。':'尚未读取到作品数据。'}<button className="text-button" onClick={refresh} disabled={updating}>重试更新</button></div>}
        {!['library','creative'].includes(view)&&projectContext}
        {!data?<div className="loading-state"><span className="loading-mark"/><h2>正在连接本地片场</h2><p>读取作品清单、素材与任务记录…</p><button className="text-button" onClick={refresh}>重试读取</button></div>:view==='library'?<LibraryView data={data} navigate={navigate} token={reviewToken} writable={Boolean(verified&&data.review_writes&&reviewToken)} refresh={refresh}/>:!project?<Empty icon="folder" title={verified?"作品不存在":"正在核对指定作品…"}>可返回作品库选择作品。</Empty>:<div className="view-content" key={view+project.key}>{view==='creative'?<CreativeCanvas projectContext={projectContext} canvasTheme={theme} project={project} ui={CANVAS_UI} openReviews={openReviews} token={reviewToken} writable={Boolean(verified && data.review_writes && reviewToken)} refresh={refresh}/>:view==='reviews'?<ReviewBoard project={project} token={reviewToken} writable={Boolean(verified && data.review_writes && reviewToken)} refresh={refresh} ui={CANVAS_UI} initialSubject={reviewSubject}/>:view==='pipeline'?<PipelineView project={project} navigate={navigate} inspect={setInspected}/>:view==='assets'?<AssetsView project={project} inspect={setInspected}/>:view==='clips'?<ClipsView project={project} inspect={setInspected}/>:view==='tasks'?<TasksView data={data} project={project} inspect={setInspected}/>:<TimelineView data={data} project={project}/>}</div>}
      </main><footer className="page-footer"><span><span className="footer-cross">✳</span> 漫剧创作工作台</span><span className="mono">LOCAL REVIEW / 上次读取 {readAt?date(new Date(readAt).toISOString()):'—'}</span></footer></div>
      {inspected&&<Inspector key={inspected.id+inspected.kind} item={inspected} onClose={()=>setInspected(null)}/>}
      {showIssues&&<Dialog title="本地数据提示" subtitle="DATA NOTES / 只读核对" onClose={()=>setShowIssues(false)}><div className="issues-list">{data?.issues.length?data.issues.map((i,index)=><div key={index}><Icon name="info" size={18}/><span>{i.message}{i.project&&<small>{i.project}</small>}</span></div>):<Empty compact title="暂无额外数据提示">这不代表作品已经通过生产验收。</Empty>}</div></Dialog>}
    </div>;
}

createRoot(document.getElementById('root')).render(<ImageViewerProvider><App/></ImageViewerProvider>);
