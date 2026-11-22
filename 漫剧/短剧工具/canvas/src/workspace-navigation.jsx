import React from 'react';
import {workRoute} from './work-routes.js';

const primary = [['creative','创作画布','nodes'],['assets','资产库','grid'],['tasks','任务与花费','activity']];
const more = [['pipeline','制作流水线','pipeline'],['timeline','事件时间线','clock']];

export function HomeBrand({Icon}) {
  return <a className="home-brand" href="#/library" aria-label="漫剧作品库首页">
    <span className="brand-symbol"><Icon name="play" size={23}/></span><span>漫剧<small>作品库</small></span></a>;
}

export default function WorkspaceNavigation({project,view,live,Icon}) {
  if (!project) return null;
  const link = ([id,label,icon]) => <a key={id} className={`nav-item ${view===id?'active':''}`}
    href={workRoute(id,project.key)} title={label} aria-current={view===id?'page':undefined}>
    <Icon name={icon} size={19}/><span>{label}</span></a>;
  const moreActive = more.some(([id])=>id===view);
  return <aside className="sidebar" aria-label={`${project.title}的制作工作台`}>
    <a className="brand" href="#/library" aria-label="返回作品库首页"><span className="brand-symbol"><Icon name="play" size={24}/></span>
      <span>漫剧<span className="brand-sub">AI 漫剧创作工作台</span></span></a>
    <a className="sidebar-library-link" href="#/library" title="返回作品库"><Icon name="library" size={17}/><span>返回作品库</span></a>
    <div className="workspace-label">本剧制作 <span>本地</span></div>
    <nav aria-label="本剧制作导航">{primary.map(link)}
      <details className="sidebar-more" key={moreActive?view:'main'} open={moreActive||undefined}>
        <summary className={`nav-item ${moreActive?'active':''}`} title="更多功能"><Icon name="grid" size={19}/><span>更多</span></summary>
        <div className="sidebar-more-menu">{more.map(link)}</div>
      </details>
    </nav>
    <div className="sidebar-bottom"><div className="local-mark"><span className={`live-dot ${live?'connected':''}`}/><span>本机工作区</span><small className="mono">LOCAL</small></div>
      <div className="read-note"><Icon name="lock" size={13}/><span>本地审核 · 零模型调用</span></div></div>
  </aside>;
}
