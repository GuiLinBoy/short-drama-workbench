import test from 'node:test';
import assert from 'node:assert/strict';
import {reviewPresentation, REVIEW_FOCUS} from '../src/review-presentation.js';
import {reviewSelection, selectionIsCurrent, reviewCounts, filterReviewItems, defaultReviewSelection, reviewActionAvailability} from '../src/review-model.js';

test('selected review binds the displayed version and decision head', () => {
  const item = {id: 'clip', fingerprint: 'version-1', head: null};
  const selection = reviewSelection(item);
  assert.equal(selectionIsCurrent(selection, item), true);
  assert.equal(selectionIsCurrent(selection, {...item, fingerprint: 'version-2'}), false);
  assert.equal(selectionIsCurrent(selection, {...item, head: 'another-decision'}), false);
  assert.equal(selectionIsCurrent(selection, undefined), false);
});
test('director status cannot count as user approval', () => {
  assert.deepEqual(reviewCounts([{status:'pending'}, {status:'stale'}, {status:'blocked'}, {status:'approved'}]),
    {pending:3, approved:1, changed:2});
  assert.deepEqual(reviewCounts(), {pending:0, approved:0, changed:0});
});

const record=(id,status,extra={})=>({id,title:id,subject:'EP001',stage:2,status,fingerprint:'version-'+id,head:null,canApprove:true,canResume:status==='deferred',...extra});
test('automatic selection includes pending only and never silently approves holds or requested changes',()=>{
  const items=[record('pending','pending'),record('hold','deferred'),record('change','changes_requested'),
    record('old','stale'),record('pass','approved'),record('blocked','pending',{canApprove:false})];
  assert.deepEqual(Object.keys(defaultReviewSelection(items)),['pending']);
});
test('held filter also finds an active hold whose material has changed',()=>{
  const items=[record('held','deferred'),record('changed-held','stale',{canResume:true}),record('other','pending')];
  assert.deepEqual(filterReviewItems(items,{state:'deferred'}).map(i=>i.id),['held','changed-held']);
  assert.deepEqual(filterReviewItems(items,{query:'other'}).map(i=>i.id),['other']);
});
test('hidden or stale selections cannot submit any review action',()=>{
  const items=[record('one','pending'),record('two','deferred')];
  const selected=items.map(reviewSelection);
  let state=reviewActionAvailability(items,selected,[items[0]]);
  assert.equal(state.hiddenCount,1);assert.equal(state.canAct,false);assert.equal(state.canApprove,false);assert.equal(state.canResume,false);
  state=reviewActionAvailability([{...items[0],head:'new-event'},items[1]],selected,items);
  assert.equal(state.changed,true);assert.equal(state.canAct,false);
});
test('cancel defer requires every selected item to have an active hold',()=>{
  const items=[record('one','deferred'),record('two','pending')];
  assert.equal(reviewActionAvailability(items,[reviewSelection(items[0])],items).canResume,true);
  assert.equal(reviewActionAvailability(items,items.map(reviewSelection),items).canResume,false);
  assert.equal(reviewActionAvailability(items,[],items).canResume,false);
});

test('mixed upstream/downstream selection explains blockers and offers only existing valid ready selections',()=>{
  const image=record('image','pending');
  const video=record('video','pending',{canApprove:false,dependsOn:['image'],problems:['上游尚未确认：image']});
  const items=[image,video],selected=items.map(reviewSelection);
  const state=reviewActionAvailability(items,selected,items);
  assert.equal(state.canApprove,false);
  assert.deepEqual(state.approvalBlocked,[video]);
  assert.deepEqual(state.approvableSelection,[selected[0]]);
  assert.equal(reviewActionAvailability(items,state.approvableSelection,items).canApprove,true);
  assert.deepEqual(reviewActionAvailability(items,selected,[video]).approvableSelection,[]);
  assert.deepEqual(reviewActionAvailability([{...image,head:'new-decision'},video],selected,items).approvableSelection,[]);
  assert.equal(video.canApprove,false);
  assert.equal(image.status,'pending');
});

test('all six stages and exceptions have audience-facing review questions', () => {
  for (const kind of ['plan','storyboard','image_prompt','image','audio','video_prompt','video','final','exception']) {
    assert.ok(REVIEW_FOCUS[kind]?.length >= 2, kind);
  }
  assert.match(REVIEW_FOCUS.plan.join(''), /预算金额/);
  assert.match(REVIEW_FOCUS.exception.join(''), /新增费用/);
});
test('image and video results display only their direct formal prompt with its status', () => {
  for (const [kind,promptKind] of [['image','image_prompt'],['video','video_prompt']]) {
    const prompt = {id:'prompt',kind:promptKind,status:'stale',request:{prompt:'真实请求原文'},files:[]};
    const unrelated = {...prompt,id:'other',request:{prompt:'无关原文'}};
    const item = {kind,dependsOn:['prompt'],files:[]};
    const before = JSON.stringify([item,prompt,unrelated]);
    const view = reviewPresentation(item,[prompt,unrelated]);
    assert.deepEqual(view.prompts,[prompt]);
    assert.equal(view.prompts[0].status,'stale');
    assert.equal(JSON.stringify([item,prompt,unrelated]),before);
    assert.deepEqual(reviewPresentation({...item,dependsOn:['missing']},[prompt]).prompts,[]);
  }
});
test('technical reports stay accessible while media, scripts and budgets remain primary', () => {
  const image = {path:'角色/王叔.png',resource:{mime:'image/png'}};
  const report = {label:'导演实际看图检查与版本说明',text:'全部证据保留'};
  const script = {label:'拟采用剧本v002',text:'实际台词'};
  const budget = {label:'媒体预算提案',text:'金额和次数'};
  const prompt = {label:'中文图片提示词',text:'实际提示词'};
  const view = reviewPresentation({kind:'plan',files:[report,image,script,budget,prompt]});
  assert.deepEqual(view.media,[image]);
  assert.deepEqual(view.primary,[script,budget,prompt]);
  assert.deepEqual(view.supporting,[report]);
});
