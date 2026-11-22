import test from 'node:test';
import assert from 'node:assert/strict';
import {buildCreativeGraph} from '../src/canvas-model.js';
import {latestCanvasReviews} from '../src/canvas-reviews.js';
import {clipTiming,timingLines} from '../src/clip-timing.js';
import {presentCanvasGraph} from '../src/canvas-presentation.js';

test('adopted time belongs to exact approved video; replacement prompt only displays plan',()=>{
  const adopted={duration:118/24,path:'cut.mp4',sha256:'sha'};
  const record={duration:9,timing:{planned:5,adopted}};
  const approved={kind:'video',status:'approved',files:[{path:'cut.mp4',sha256:'sha',resource:{exists:true}}]};
  assert.deepEqual(timingLines(clipTiming(record,approved)),['已采用 4.92 秒','原生成请求 9 秒']);
  assert.deepEqual(timingLines(clipTiming(record,{kind:'video_prompt',status:'draft'})),['计划采用 5 秒','拟生成 9 秒']);
  assert.equal(clipTiming(record,{...approved,status:'stale'}).adopted,null);
  assert.equal(clipTiming(record,{...approved,files:[{path:'cut.mp4',sha256:'changed',resource:{exists:true}}]}).adopted,null);
});
const entry=(id,kind,subject,date,extra={})=>({id,kind,subject,title:id,submittedAt:`2026-11-10T${date}:00+08:00`,
  status:'pending',fingerprint:id+'-sha',head:null,canApprove:true,files:[],dependsOn:[],...extra});
const fixture=items=>({key:'p',title:'作品',assets:[],clips:[],finals:[],reviews:{items}});
const media=(mime,url)=>({path:url,resource:{exists:true,mime,url}});

test('independent composition image stays visible alongside its approved video and exact dependency',()=>{
  const clip='EP001/Clip00001';
  const prompt=entry('frame-prompt','image_prompt',clip,'09:00',{request:{prompt:'构图',params:{}}});
  const frame=entry('frame','image',clip,'09:10',{status:'approved',dependsOn:[prompt.id],files:[media('image/png','/frame.png')]});
  const vp=entry('video-prompt','video_prompt',clip,'10:00',{dependsOn:[frame.id],request:{prompt:'说话',params:{duration:4}}});
  const video=entry('video','video',clip,'10:10',{status:'approved',dependsOn:[vp.id],files:[media('video/mp4','/approved.mp4')]});
  const input=fixture([video,prompt,frame,vp]);
  input.clips=[{id:clip,episode:'EP001',assets:[]}];
  input.assets=[{id:'S00003',name:'开场构图',type:'scene'}];
  input.creative_asset_aliases={S00003:clip}; // Explicit separation takes precedence over a legacy alias.
  input.creative_clip_reference_images={[clip]:'S00003'};
  const before=structuredClone(input);
  for (const unused of [false,true]) {
    const graph=presentCanvasGraph(buildCreativeGraph(input,{episode:'EP001',unused}));
    assert.equal(graph.nodes.length,2);
    const image=graph.nodes.find(n=>n.id==='asset:S00003'),movie=graph.nodes.find(n=>n.id===`video:${clip}`);
    assert.equal(image.data.kind,'image');
    assert.equal(image.data.title,'开场构图参考图 · Clip00001');
    assert.equal(image.data.preview.url,'/frame.png');
    assert.deepEqual(image.data.reviewItems.map(i=>i.id),[frame.id]);
    assert.equal(movie.data.record.video.url,'/approved.mp4');
    assert.deepEqual(movie.data.reviewItems.map(i=>i.id),[video.id]);
    assert.equal(graph.reviewNodeById[frame.id],image.id);
    const links=graph.edges.filter(e=>e.source===image.id && e.target===movie.id);
    assert.equal(links.length,1);
    assert.ok(links[0].links.some(e=>e.reviewId===frame.id && !e.historical));
  }
  assert.deepEqual(input,before);
  // Revising the image alone neither revives an old image nor withdraws an adopted video.
  input.reviews.items.push(entry('new-image-prompt','image_prompt',clip,'11:00',{request:{prompt:'新构图',params:{}}}));
  const graph=buildCreativeGraph(input);
  assert.equal(graph.nodes.find(n=>n.id==='asset:S00003').data.preview,null);
  assert.equal(graph.nodes.find(n=>n.id===`video:${clip}`).data.record.video.url,'/approved.mp4');
  assert.ok(graph.edges.some(e=>e.reviewId===frame.id && e.historical));
});

test('separating a reference keeps an explicitly withdrawn video hidden until its new prompt arrives',()=>{
  const clip='EP001/Clip00002';
  const oldPrompt=entry('old-prompt','video_prompt',clip,'09:00',{request:{prompt:'旧动作',params:{duration:7}}});
  const oldVideo=entry('old-video','video',clip,'09:10',{dependsOn:[oldPrompt.id],files:[media('video/mp4','/failed.mp4')]});
  const imagePrompt=entry('frame-prompt','image_prompt',clip,'10:00',{request:{prompt:'双人构图',params:{}}});
  const frame=entry('frame','image',clip,'10:10',{title:'双人构图参考图 · 当前版本',dependsOn:[imagePrompt.id],files:[media('image/png','/frame.png')]});
  const input=fixture([oldPrompt,oldVideo,imagePrompt,frame]);
  input.clips=[{id:clip,episode:'EP001',assets:[],duration:7,timing:{planned:5},
    video:{exists:true,url:'/failed.mp4'},ending_frame:{exists:true,url:'/failed.png'},
    draft:{video:{exists:true,url:'/failed.mp4'}},prompt:{exists:true,url:'/old.txt'}}];
  input.assets=[{id:'S00004',name:'双人构图',type:'scene'}];
  input.creative_clip_reference_images={[clip]:'S00004'};
  input.creative_withdrawn_video_through={[clip]:oldVideo.id};
  const before=structuredClone(input);
  function view(){const graph=presentCanvasGraph(buildCreativeGraph(input));return {graph,image:graph.nodes.find(n=>n.id==='asset:S00004').data,video:graph.nodes.find(n=>n.id===`video:${clip}`).data};}
  let {graph,image,video}=view();
  assert.equal(graph.nodes.length,2);
  assert.equal(image.preview.url,'/frame.png');
  assert.equal(image.activeReview.status,'pending');
  assert.equal(image.title,'双人构图参考图 · Clip00002');
  assert.equal(video.awaitingPrompt,true);
  assert.equal(video.activeReview,null);assert.equal(video.currentPrompt,null);
  assert.equal(video.record.video,null);assert.equal(video.record.draft,null);
  assert.equal(video.preview,null);assert.equal(video.record.prompt,null);
  assert.equal(video.hiddenVersions,2);
  assert.deepEqual(timingLines(clipTiming(video.record)),['计划采用 5 秒']);
  assert.ok(!graph.currentReviewItems.some(i=>i.id===oldVideo.id));
  assert.ok(graph.edges.some(e=>e.source==='asset:S00004' && e.target===`video:${clip}`));
  assert.deepEqual(input,before);
  input.reviews.items.push(entry('new-prompt','video_prompt',clip,'11:00',{dependsOn:[frame.id],request:{prompt:'新动作',params:{duration:4}}}));
  ({image,video}=view());
  assert.equal(image.preview.url,'/frame.png');assert.equal(video.activeReview.id,'new-prompt');
  assert.equal(video.awaitingGeneration,true);assert.equal(video.record.video,null);
  input.reviews.items.push(entry('new-video','video',clip,'11:10',{dependsOn:['new-prompt'],files:[media('video/mp4','/new.mp4')]}));
  ({graph,image,video}=view());
  assert.equal(graph.nodes.length,2);assert.equal(image.preview.url,'/frame.png');
  assert.equal(video.record.video.url,'/new.mp4');
  assert.deepEqual(video.reviewItems.map(i=>i.id),['new-video']);
  assert.equal(input.reviews.items.find(i=>i.id===oldVideo.id).status,'pending');
});

test('pending submissions create real canvas nodes and dependency wires before manifest registration',()=>{
  const image=entry('image','image','S00001','09:00',{files:[media('image/png','/image.png')]});
  const prompt=entry('prompt','video_prompt','EP001/Clip00001','10:00',{dependsOn:['image'],request:{prompt:'真实中文提示词',params:{duration:8}}});
  const input=fixture([image,prompt]),before=structuredClone(input);
  const graph=buildCreativeGraph(input);
  const video=graph.nodes.find(node=>node.id==='video:EP001/Clip00001');
  assert.equal(video.data.currentPrompt.request.prompt,'真实中文提示词');
  assert.equal(video.data.awaitingGeneration,true);
  assert.ok(graph.edges.some(edge=>edge.source==='asset:S00001' && edge.target===video.id));
  assert.deepEqual(input,before);
});
test('one segment displays only the newest prompt and result, independent of input array order',()=>{
  const p1=entry('prompt-v099','video_prompt','EP001/Clip00001','09:00',{request:{prompt:'旧',params:{}}});
  const r1=entry('result-old','video','EP001/Clip00001','09:10',{files:[media('video/mp4','/old.mp4')],dependsOn:[p1.id]});
  const p2=entry('prompt-new','video_prompt','EP001/Clip00001','10:00',{request:{prompt:'新',params:{}}});
  const r2=entry('result-new','video','EP001/Clip00001','10:10',{files:[media('video/mp4','/new.mp4')],dependsOn:[p2.id]});
  const input=fixture([r2,p1,p2,r1]);
  const graph=buildCreativeGraph(input);
  assert.equal(graph.nodes.filter(node=>node.data.kind==='video').length,1);
  const node=graph.nodes.find(node=>node.data.kind==='video');
  assert.equal(node.data.record.video.url,'/new.mp4');
  assert.equal(node.data.currentPrompt.id,p2.id);
  assert.deepEqual(node.data.reviewItems.map(item=>item.id),[r2.id]);
  assert.equal(input.reviews.items.length,4);
  assert.equal(node.data.hiddenVersions,3);
});

test('explicit repair aliases share one node, hide the old result and keep real variants separate',()=>{
  const old=entry('old','image','S00001','09:00',{files:[media('image/png','/old.png')]});
  const source=entry('source','image','S00001-edit-source','09:30',{dependsOn:['old'],files:old.files});
  const prompt=entry('repair','image_prompt','S00002','10:00',{dependsOn:['source'],request:{prompt:'只留一扇门',params:{}}});
  const input=fixture([old,source,prompt]);
  input.assets=[{id:'S00001',name:'客厅',type:'scene',image:old.files[0].resource},
    {id:'S00002',name:'单门修正',type:'scene',source_asset_id:'S00001'},
    {id:'S00003',name:'夜晚客厅',type:'scene',source_asset_id:'S00001'}];
  input.creative_asset_aliases={'S00002':'S00001','S00001-edit-source':'S00001'};
  const before=structuredClone(input),graph=buildCreativeGraph(input,{unused:true});
  const room=graph.nodes.find(n=>n.id==='asset:S00001');
  assert.equal(graph.nodes.filter(n=>n.data.kind==='asset').length,2);
  assert.equal(room.data.preview,null);
  assert.equal(room.data.currentPrompt.id,'repair');
  assert.deepEqual(room.data.reviewItems.map(i=>i.id),['repair']);
  assert.ok(graph.edges.every(e=>e.source!==e.target));
  assert.equal(graph.reviewNodeById.source,room.id);
  assert.deepEqual(input,before);
  input.reviews.items.push(entry('repaired','image','S00002','11:00',{dependsOn:['repair'],files:[media('image/png','/new.png')]}));
  const next=buildCreativeGraph(input,{unused:true}).nodes.find(n=>n.id===room.id);
  assert.equal(next.data.preview.url,'/new.png');
  assert.deepEqual(next.data.reviewItems.map(i=>i.id),['repaired']);
});

test('rejecting a result immediately clears the visible output without deleting history',()=>{
  for (const kind of ['image','video']) {
    const item=entry('rejected',kind,kind==='image'?'S00001':'EP001/Clip00001','10:00',
      {status:'changes_requested',files:[media(kind==='image'?'image/png':'video/mp4','/rejected')]});
    const input=fixture([item]),before=structuredClone(input);
    const node=buildCreativeGraph(input).nodes.find(n=>n.data.activeReview?.id===item.id);
    assert.equal(node.data.preview,null);
    assert.equal(node.data.record[kind==='image'?'image':'video'],null);
    assert.deepEqual(input,before);
  }
});
test('new prompt does not masquerade an old approved video as the new result',()=>{
  const p1=entry('p1','video_prompt','EP001/Clip00001','09:00',{request:{prompt:'旧',params:{}}});
  const r1=entry('r1','video','EP001/Clip00001','09:10',{status:'approved',files:[media('video/mp4','/old.mp4')],dependsOn:['p1']});
  const p2=entry('p2','video_prompt','EP001/Clip00001','10:00',{request:{prompt:'新',params:{duration:12}}});
  const input=fixture([p1,r1,p2]);
  input.clips=[{id:'EP001/Clip00001',episode:'EP001',assets:[],status:'approved',video:{exists:true,url:'/old.mp4'},ending_frame:{exists:true,url:'/old.png'}}];
  const graph=buildCreativeGraph(input),node=graph.nodes.find(node=>node.data.kind==='video');
  assert.equal(node.data.activeReview.id,'p2');
  assert.equal(node.data.record.video,null);
  assert.equal(node.data.preview,null);
  assert.deepEqual(node.data.reviewItems.map(item=>item.id),['p2']);
  assert.equal(input.clips[0].video.url,'/old.mp4');
});
test('same clip number in different episodes and independent exception requests are not collapsed',()=>{
  const items=[entry('a','video_prompt','EP001/Clip00001','09:00'),entry('b','video_prompt','EP002/Clip00001','10:00'),
    entry('fee1','exception','EP001','09:00'),entry('fee2','exception','EP001','10:00')];
  assert.equal(latestCanvasReviews(items).length,4);
  const graph=buildCreativeGraph(fixture(items),{episode:'EP001'});
  assert.ok(!graph.nodes.some(node=>node.id==='video:EP002/Clip00001'));
  assert.equal(graph.nodes.filter(node=>node.data.activeReview?.kind==='exception').length,2);
});
test('historical dependency remains explicitly labelled and never rewrites the original dependency',()=>{
  const old=entry('image-old','image','S00001','09:00',{status:'approved',files:[media('image/png','/old.png')]});
  const fresh=entry('image-new','image','S00001','10:00',{files:[media('image/png','/new.png')]});
  const prompt=entry('prompt','video_prompt','EP001/Clip00001','10:30',{dependsOn:['image-old'],request:{prompt:'视频',params:{}}});
  const input=fixture([fresh,prompt,old]),graph=buildCreativeGraph(input);
  const edge=graph.edges.find(edge=>edge.reviewId==='image-old');
  assert.equal(edge.label,'历史版本依赖');assert.equal(edge.historical,true);
  assert.deepEqual(prompt.dependsOn,['image-old']);
  assert.equal(graph.nodes.find(node=>node.id==='asset:S00001').data.activeReview.status,'pending');
});
test('result nodes retain reference wires from their own prompt after prompt/result collapse',()=>{
  const image=entry('image','image','S00001','09:00',{files:[media('image/png','/room.png')]});
  const prompt=entry('prompt','video_prompt','EP001/Clip00001','10:00',{dependsOn:['image'],request:{prompt:'实际提示词',params:{}}});
  const result=entry('result','video','EP001/Clip00001','11:00',{dependsOn:['prompt'],files:[media('video/mp4','/result.mp4')]});
  const graph=buildCreativeGraph(fixture([image,prompt,result]));
  assert.ok(graph.edges.some(edge=>edge.source==='asset:S00001' && edge.target==='video:EP001/Clip00001' && edge.reviewId==='image'));
  assert.ok(graph.edges.every(edge=>edge.source!==edge.target));
});

test('storyboard reviews fold into videos with direct reference wires and exact review IDs preserved',()=>{
  const image=entry('ref','image','S00001','09:00');
  const story=entry('boards','storyboard','EP001','09:10',{dependsOn:['ref']});
  const prompt=entry('p','video_prompt','EP001/Clip00001','10:00',{dependsOn:['boards'],request:{prompt:'融合分镜的正式提示词',params:{}}});
  const other=entry('p2','video_prompt','EP001/Clip00002','10:00',{dependsOn:['boards'],request:{prompt:'第二段',params:{}}});
  const input=fixture([image,story,prompt,other]),before=structuredClone(input),graph=buildCreativeGraph(input);
  assert.ok(!graph.nodes.some(node=>node.data.kind==='story' || node.data.activeReview?.kind==='storyboard'));
  for (const id of ['video:EP001/Clip00001','video:EP001/Clip00002']) {
    const video=graph.nodes.find(node=>node.id===id);
    assert.ok(video.data.reviewItems.some(item=>item.id==='boards'));
    assert.ok(graph.edges.some(edge=>edge.source==='asset:S00001' && edge.target===id && edge.reviewId==='ref'));
  }
  assert.ok(!graph.edges.some(edge=>edge.source.startsWith('video:') && edge.target.startsWith('video:')));
  assert.equal(graph.currentReviewItems.filter(item=>item.id==='boards').length,1);
  assert.ok(graph.nodes.find(node=>node.id===graph.reviewNodeById.boards).data.reviewItems.some(item=>item.id==='boards'));
  assert.deepEqual(input,before);
});


test('clip composition image shares the original video node through prompt, image and video stages',()=>{
  const old=entry('old','video','EP001/Clip00001','09:00',{files:[media('video/mp4','/old.mp4')]});
  const prompt=entry('frame-prompt','image_prompt','EP001/Clip00001','10:00',{request:{prompt:'抬好左腕，完整头部',params:{n:1}}});
  const p=fixture([old,prompt]);
  p.clips=[{id:'EP001/Clip00001',episode:'EP001',assets:['S00003'],duration:9,video:{exists:true,url:'/old.mp4'}}];
  p.assets=[{id:'S00003',name:'开场构图',type:'scene',image:{exists:true,url:'/internal.png'}}];
  p.creative_asset_aliases={S00003:'EP001/Clip00001'};
  const before=structuredClone(p);
  function current(){const g=buildCreativeGraph(p,{unused:true});assert.equal(g.nodes.filter(n=>n.id==='video:EP001/Clip00001').length,1);assert.ok(!g.nodes.some(n=>n.id==='asset:S00003'||n.id==='asset:EP001/Clip00001'));return g.nodes.find(n=>n.id==='video:EP001/Clip00001').data;}
  let n=current();assert.equal(n.kind,'image');assert.equal(n.title,'开场构图参考图 · Clip00001');assert.equal(n.activeReview.id,prompt.id);assert.equal(n.record.video,null);assert.equal(n.preview,null);assert.deepEqual(n.reviewItems.map(i=>i.id),[prompt.id]);assert.deepEqual(p,before);
  p.reviews.items.push(entry('frame','image','EP001/Clip00001','10:10',{dependsOn:[prompt.id],files:[media('image/png','/frame.png')]}));
  n=current();assert.equal(n.kind,'image');assert.equal(n.title,'开场构图参考图 · Clip00001');assert.equal(n.record.image.url,'/frame.png');assert.equal(n.preview.url,'/frame.png');assert.equal(n.record.video,null);
  p.reviews.items.push(entry('new-video-prompt','video_prompt','EP001/Clip00001','10:20',{dependsOn:['frame'],request:{prompt:'站定说话',params:{duration:9}}}));
  n=current();assert.equal(n.kind,'video');assert.equal(n.activeReview.id,'new-video-prompt');assert.equal(n.preview,null);
});

test('second clip composition keeps one image node and removes the failed video preview',()=>{
  const old=entry('failed-video','video','EP001/Clip00002','09:00',{files:[media('video/mp4','/failed.mp4')]});
  const prompt=entry('two-person-prompt','image_prompt','EP001/Clip00002','10:00',{
    title:'双人构图参考图 · 完整头部与交杯手位',request:{prompt:'王叔左张姨右，头部完整',params:{n:1}}});
  const p=fixture([old,prompt]);
  p.clips=[{id:'EP001/Clip00002',episode:'EP001',assets:['S00004'],video:{exists:true,url:'/failed.mp4'}}];
  p.assets=[{id:'S00004',name:'双人构图',type:'scene',image:{exists:false}}];
  p.creative_asset_aliases={S00004:'EP001/Clip00002'};
  const before=structuredClone(p);
  const graph=buildCreativeGraph(p,{unused:true});
  const nodes=graph.nodes.filter(n=>n.id==='video:EP001/Clip00002');
  assert.equal(nodes.length,1);
  assert.ok(!graph.nodes.some(n=>n.id==='asset:S00004'||n.id==='asset:EP001/Clip00002'));
  const d=nodes[0].data;
  assert.equal(d.kind,'image');
  assert.equal(d.title,'双人构图参考图 · Clip00002');
  assert.equal(d.record.video,null);assert.equal(d.preview,null);
  assert.deepEqual(d.reviewItems.map(x=>x.id),[prompt.id]);
  assert.deepEqual(p,before);
});
