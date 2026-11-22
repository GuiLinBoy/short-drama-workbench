import React,{useEffect,useState} from 'react';
import {scheduleVideoThumbnail} from './media-queue.js';

const cache=new Map();
// Decode a single frame locally; the canvas node itself never contains a player.
export default function VideoThumbnail({resource,fallback}) {
  const key=JSON.stringify([resource.url,resource.size,resource.modified,resource.sha256]);
  const [result,setResult]=useState(()=>({key,frame:cache.get(key)||null,failed:false}));
  const frame=result.key===key?result.frame:cache.get(key);
  const failed=result.key===key && result.failed;
  useEffect(()=>{
    if(cache.has(key)){setResult({key,frame:cache.get(key),failed:false});return;}
    setResult({key,frame:null,failed:false});
    const fail=()=>setResult({key,frame:null,failed:true});
    let disposed=false;
    const cancel=scheduleVideoThumbnail(finish=>{
    const video=document.createElement('video');
    const timer=setTimeout(()=>{if(!disposed)fail();finish();},12000);
    function release(){clearTimeout(timer);video.onloadedmetadata=null;video.onseeked=null;video.onerror=null;video.removeAttribute('src');video.load();}
    video.muted=true;video.playsInline=true;video.preload='metadata';
    video.onloadedmetadata=()=>{video.currentTime=Math.min(.5,Number.isFinite(video.duration)?video.duration/2:.5);};
    video.onseeked=()=>{
      if(disposed)return;
      try {
        if(!video.videoWidth || !video.videoHeight){fail();finish();return;}
        const canvas=document.createElement('canvas'),scale=Math.min(1,480/video.videoWidth,480/video.videoHeight);
        canvas.width=Math.max(1,Math.round(video.videoWidth*scale));canvas.height=Math.max(1,Math.round(video.videoHeight*scale));
        canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
        const url=canvas.toDataURL('image/jpeg',.82);
        if(cache.size>=64)cache.delete(cache.keys().next().value);
        cache.set(key,url);setResult({key,frame:url,failed:false});
      } catch {fail();}
      finish();
    };
    video.onerror=()=>{if(!disposed)fail();finish();};video.src=resource.url;
    return release;
    });
    return()=>{disposed=true;cancel();};
  },[key,resource.url]);
  return frame?<img className="video-node-thumbnail" src={frame} alt="已生成视频截图"/>:failed?<div className="node-no-preview"><span>截图暂不可用 · 点击查看详情</span></div>:fallback;
}
