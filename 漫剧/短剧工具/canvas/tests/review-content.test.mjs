import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {buildSync} from 'esbuild';

// Render the actual JSX, not a second implementation of the display rules.
const compiled = buildSync({entryPoints:[fileURLToPath(new URL('../src/review-board.jsx',import.meta.url))],
  bundle:true,write:false,platform:'node',format:'cjs',packages:'external'}).outputFiles[0].text;
const mod = {exports:{}};
new Function('require','module','exports',compiled)(createRequire(import.meta.url),mod,mod.exports);
const Board = mod.exports.default;
const ui = {Icon:()=>null,Empty:({title})=>React.createElement('p',null,title),date:()=>'',
  MediaPreview:({resource})=>React.createElement(resource?.mime?.startsWith('audio/')?'audio':resource?.mime?.startsWith('video/')?'video':'img',{src:resource?.url,alt:'实际素材',controls:resource?.mime?.startsWith('audio/')||undefined})};
const base = {id:'result',title:'本次送审',fingerprint:'unchanged-version',head:null,status:'pending',canApprove:true,
  summary:'请确认外观；本次存在轻侧转偏差。',problems:[],dependsOn:[],history:[],files:[],stage:3};
const render = items => renderToStaticMarkup(React.createElement(Board,{project:{key:'local',reviews:{items,history:[]}},ui,writable:true}));

test('rejected output and exact repair input are not rendered as current images',()=>{
  const file={path:'old.png',resource:{mime:'image/png',url:'/rejected.png'}};
  const rejected=render([{...base,kind:'image',status:'changes_requested',files:[file]}]);
  assert.doesNotMatch(rejected,/<img[^>]+rejected/);
  assert.match(rejected,/旧图或视频已从当前展示撤下/);
  const repair=render([{...base,kind:'image_prompt',files:[file],editSource:{path:'old.png'},request:{prompt:'保留一扇门',params:{}}}]);
  assert.doesNotMatch(repair,/<img[^>]+rejected/);
  assert.match(repair,/保留一扇门/);
});

test('all review kinds render with concise questions and intact source content',()=>{
  for (const kind of ['plan','storyboard','image_prompt','image','audio','video_prompt','video','final','exception']) {
    const html = render([{...base,kind,files:[{label:'专业执行证据',path:'证据.md',text:'TECHNICAL-ORIGINAL'},
      {label:'剧本与预算方案',path:'剧本.md',text:'CREATIVE-AND-BUDGET'}]}]);
    assert.match(html,/本次你只需确认/);
    assert.match(html,/轻侧转偏差/);
    assert.match(html,/CREATIVE-AND-BUDGET/);
    assert.match(html,/<details><summary>专业说明与技术附件/);
    assert.match(html,/TECHNICAL-ORIGINAL/);
    assert.doesNotMatch(html,/<details[^>]*\bopen(?:=|>)/);
  }
});
test('result shows corresponding formal prompt, actual media and a warning for stale upstream',()=>{
  const prompt = {...base,id:'prompt',kind:'video_prompt',status:'stale',request:{prompt:'原始中文动作与对白',params:{duration:8}},generation:{}};
  const html = render([{...base,kind:'video',dependsOn:['prompt'],files:[{label:'实际视频',path:'clip.mp4',resource:{mime:'video/mp4',url:'/api/media/example'}}]},prompt]);
  assert.match(html,/对应提示词/);
  assert.match(html,/原始中文动作与对白/);
  assert.match(html,/<video src="\/api\/media\/example"/);
  assert.match(html,/关联提示词当前未通过或已变化/);
  assert.match(html,/生成参数与请求记录（备查）/);
});
test('blockers and missing prompt remain explicit; empty queue is usable',()=>{
  const html=render([{...base,kind:'image',canApprove:false,problems:['上游版本已改变']}]);
  assert.match(html,/通过前需要处理/);
  assert.match(html,/上游版本已改变/);
  assert.match(html,/未关联可直接展示的正式提示词/);
  assert.match(render([]),/尚无送审材料/);
});

test('unproduced images and videos show prompts first with inline reference media',()=>{
  for(const [kind,label] of [['video_prompt','视频'],['image_prompt','图片']]) {
    const html=render([{...base,kind,request:{prompt:'完整生成提示词',params:{}},files:[
      {label:'S00006',path:'reference.png',resource:{mime:'image/png',url:'/reference.png'}}]}]);
    assert.ok(html.includes(`${label}尚未生成`));
    assert.match(html,/参考图 · S00006/);
    assert.match(html,/<section class="review-section review-references"/);
    assert.ok(html.indexOf('完整生成提示词')<html.indexOf('review-references'));
    assert.ok(html.indexOf('<img src="/reference.png"')>html.indexOf('review-references'));
    assert.doesNotMatch(html,/<details[^>]*\bopen(?:=|>)/);
  }
});

test('draft references expose real audio, pending sources and no duplicate image',()=>{
  const html=render([{...base,kind:'video_prompt',status:'draft',canApprove:false,
    draft:{prompt:'draft.txt',pending:['整理音色样本'],references:[
      {id:'portrait',kind:'image',label:'张姨',status:'available',path:'a.png',source:'已通过人物图',note:'保持身份'},
      {id:'voice',kind:'audio',label:'张姨原音',status:'candidate',path:'a.wav',source:'第01段采用版',note:'音色待确认'},
      {id:'tail',kind:'tail',label:'递水尾帧',status:'pending',source:'第02段待采用视频',note:'待采用后提取'}]},
    files:[{path:'draft.txt',text:'PROMPT-FIRST'},
      {path:'a.png',resource:{exists:true,mime:'image/png',url:'/a.png'}},
      {path:'a.wav',resource:{exists:true,mime:'audio/wav',url:'/a.wav'}}]}]);
  assert.ok(html.indexOf('PROMPT-FIRST')<html.indexOf('review-references'));
  assert.equal((html.match(/<img src="\/a.png"/g)||[]).length,1);
  assert.match(html,/<audio[^>]*src="\/a.wav"[^>]*controls=""/);
  assert.match(html,/可试听 · 待定音/);
  assert.match(html,/真实尾帧尚未就绪/);
  assert.match(html,/来源：第02段待采用视频/);
  assert.doesNotMatch(html,/<details[^>]*class="[^"]*review-references/);
});

test('video drafts display complete text and explicit dependencies without approval authority',()=>{
  const html=render([{...base,kind:'video_prompt',status:'draft',canApprove:false,
    draft:{prompt:'draft.txt',pending:['等待Clip00002真实尾帧']},
    problems:['草稿尚未形成正式请求'],
    files:[{path:'draft.txt',label:'完整视频提示词草稿',text:'subject_definitions:\nFULL-DRAFT-TEXT'}]}]);
  assert.match(html,/FULL-DRAFT-TEXT/);
  assert.match(html,/等待Clip00002真实尾帧/);
  assert.match(html,/当前可查看和提意见，尚不能批准生成/);
  assert.match(html,/草稿 · 待完成准备/);
});
