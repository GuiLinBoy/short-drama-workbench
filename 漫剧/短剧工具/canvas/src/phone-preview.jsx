import React,{useEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';

const DEVICES={tall:{label:'全面屏 · 19.5:9',width:390,height:844},android:{label:'安卓长屏 · 20:9',width:412,height:915},classic:{label:'标准竖屏 · 16:9',width:360,height:640}};
const stamp=value=>`${Math.floor((value||0)/60)}:${String(Math.floor((value||0)%60)).padStart(2,'0')}`;

function PhoneDialog({resource,title,onClose}) {
  const dialog=useRef(null),video=useRef(null);
  const [device,setDevice]=useState('tall'),[overlay,setOverlay]=useState(true),[safe,setSafe]=useState(false),[fit,setFit]=useState('contain');
  const [playing,setPlaying]=useState(false),[time,setTime]=useState(0),[duration,setDuration]=useState(0),[muted,setMuted]=useState(false),[error,setError]=useState('');
  const [caption,setCaption]=useState('亲戚贵在真心，不是用来算计的。 #家庭短剧 #人情冷暖');
  useEffect(()=>{
    const element=dialog.current,player=video.current,previousFocus=document.activeElement,overflow=document.body.style.overflow;
    document.body.style.overflow='hidden';element.showModal();
    return()=>{player?.pause();element.close();document.body.style.overflow=overflow;if(previousFocus?.isConnected)previousFocus.focus({preventScroll:true});};
  },[]);
  const screen=DEVICES[device];
  async function play() {try {if(video.current.paused) await video.current.play();else video.current.pause();setError('');}catch {setError('暂时无法播放，请检查当前视频文件。');}}
  return createPortal(<dialog ref={dialog} className="phone-preview-dialog" aria-label="手机抖音播放预览" onCancel={onClose} onClick={event=>{if(event.target===dialog.current)onClose();}}>
    <header className="phone-preview-header"><div><span>观看效果预览</span><h2>把视频放进手机里看</h2></div><button className="phone-close" aria-label="关闭手机预览" onClick={onClose}>×</button></header>
    <div className="phone-preview-layout">
      <div className="phone-preview-stage"><div className="phone-device" style={{'--screen-ratio':`${screen.width} / ${screen.height}`}}>
        <div className="phone-screen">
          <video ref={video} src={resource.url} playsInline loop muted={muted} preload="metadata" style={{objectFit:fit}} aria-label="手机模拟视频"
            onLoadedMetadata={event=>setDuration(Number.isFinite(event.target.duration)?event.target.duration:0)} onTimeUpdate={event=>setTime(event.target.currentTime)}
            onPlay={()=>setPlaying(true)} onPause={()=>setPlaying(false)} onError={()=>setError('当前视频读取失败，请检查本地文件。')}/>
          {overlay && <div className="phone-app-overlay" aria-label="抖音界面模拟遮挡">
            <div className="phone-status"><span>9:41</span><span>▮▮▮  ▰</span></div><div className="phone-island"/>
            <div className="phone-app-top"><span>☰</span><span>关注</span><b>推荐</b><span>⌕</span></div>
            <div className="phone-app-actions"><div className="phone-avatar">剧<span>＋</span></div><div>♥<small>1.2万</small></div><div>●<small>326</small></div><div>★<small>收藏</small></div><div>➦<small>分享</small></div></div>
            <div className="phone-app-caption"><b>@漫剧工作台</b><p>{caption}</p><small>♫ 原声 · 家庭伦理短剧</small></div>
            <div className="phone-app-nav"><b>首页</b><span>朋友</span><strong>＋</strong><span>消息</span><span>我</span></div><div className="phone-home-bar"/>
          </div>}
          {safe && <div className="phone-safe-zone" aria-label="建议内容安全区"><span>重要人物 / 字幕建议区域</span></div>}
        </div>
      </div><span className="phone-stage-caption">本地视频 · 模拟观看界面</span></div>
      <section className="phone-preview-settings"><span className="phone-settings-kicker">预览设置</span><h3>{title || '当前视频'}</h3>
        <label>屏幕比例<select aria-label="手机屏幕比例" value={device} onChange={event=>setDevice(event.target.value)}>{Object.entries(DEVICES).map(([id,d])=><option key={id} value={id}>{d.label}</option>)}</select></label>
        <label>画面适配<select aria-label="手机画面适配" value={fit} onChange={event=>setFit(event.target.value)}><option value="contain">完整显示 · 可能留黑边</option><option value="cover">铺满屏幕 · 可能裁切</option></select></label>
        <div className="phone-toggle-list"><label><input type="checkbox" checked={overlay} onChange={event=>setOverlay(event.target.checked)}/>显示抖音模拟界面</label><label><input type="checkbox" checked={safe} onChange={event=>setSafe(event.target.checked)}/>显示建议安全区</label><label><input type="checkbox" checked={muted} onChange={event=>setMuted(event.target.checked)}/>静音播放</label></div>
        <label>模拟底部文案<textarea aria-label="模拟底部文案" value={caption} maxLength={180} onChange={event=>setCaption(event.target.value)}/></label>
        <div className="phone-playback"><button className="primary-button" onClick={play}>{playing?'暂停':'播放视频'}</button><span>{stamp(time)} / {stamp(duration)}</span><input type="range" aria-label="手机预览播放进度" min="0" max={duration||0} step="0.05" value={time} disabled={!duration} onChange={event=>{video.current.currentTime=Number(event.target.value);setTime(Number(event.target.value));}}/></div>
        {error && <p className="review-error" role="alert">{error}</p>}
        <p className="phone-preview-explainer">观察人脸、手部和字幕有没有被右侧按钮、底部文案遮住。建议安全区仅用于辅助检查。</p><p className="phone-preview-disclaimer">界面为示意，屏幕比例不代表具体机型。实际抖音界面、上传压缩与实机效果可能不同。仅本地预览，不上传视频。</p>
      </section>
    </div>
  </dialog>,document.body);
}

export default function PhonePreview({resource,title}) {
  const [open,setOpen]=useState(false);
  if (!resource?.exists) return null;
  return <><button className="phone-preview-trigger" onClick={event=>{event.currentTarget.closest('section')?.querySelector('video')?.pause();setOpen(true);}}>▯ 手机抖音预览</button>{open && <PhoneDialog resource={resource} title={title} onClose={()=>setOpen(false)}/>}</>;
}
