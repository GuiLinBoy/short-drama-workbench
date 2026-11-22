import test from 'node:test';
import assert from 'node:assert/strict';
import {createSnapshotClient} from '../src/snapshot-client.js';

const snapshot = revision => ({revision, projects:[], issues:[], ledger:[], review_writes:true});
const response = (data, tag='current') => ({ok:true, status:200, json:async()=>data,
  headers:new Map([['ETag',tag], ['X-Canvas-Review','LIVE-ONLY-TOKEN']])});
const deferred = () => {let resolve; const promise=new Promise(r=>resolve=r); return {promise,resolve};};
function storageFor(data=snapshot('cached')) {
  let raw=JSON.stringify({version:1,savedAt:1000,data});
  return {getItem:()=>raw,setItem:(_,value)=>{raw=value;},removeItem:()=>{raw=null;}};
}
const wait = () => new Promise(resolve=>setTimeout(resolve,5));

test('cached canvas remains visible during refresh and gets no cached review authority',async()=>{
  const pending=deferred(); let options;
  const storage=storageFor();
  const client=createSnapshotClient({storage,now:()=>2000,fetch:(_,opts)=>{options=opts;return pending.promise;}});
  const cached=client.getState().data;
  assert.equal(cached.revision,'cached');
  assert.equal(client.getState().verified,false);
  assert.equal(client.getState().reviewToken,'');
  const loading=client.refresh();
  assert.equal(client.getState().data,cached);
  assert.equal(client.getState().updating,true);
  assert.deepEqual(options.headers,{});
  pending.resolve(response(snapshot('fresh')));
  await loading; await wait();
  assert.equal(client.getState().data.revision,'fresh');
  assert.equal(client.getState().verified,true);
  assert.equal(client.getState().reviewToken,'LIVE-ONLY-TOKEN');
  const saved=storage.getItem();
  assert.doesNotMatch(saved,/LIVE-ONLY-TOKEN/);
  assert.equal(JSON.parse(saved).data.review_writes,false);
  const reloaded=createSnapshotClient({storage,now:()=>2001});
  assert.equal(reloaded.getState().data.revision,'fresh');
  assert.equal(reloaded.getState().verified,false);
  client.stop(); reloaded.stop();
});

test('failed update preserves current nodes, disables review authority and retries without stale etag',async()=>{
  const calls=[];let mode=0;
  const client=createSnapshotClient({fetch:async(_,options)=>{
    calls.push(options.headers);
    if(mode===1)throw new Error('offline');
    return response(snapshot(mode===0?'first':'recovered'));
  }});
  await client.refresh();const existing=client.getState().data;
  mode=1;await client.refresh();
  assert.equal(client.getState().data,existing);
  assert.equal(client.getState().error,'offline');
  assert.equal(client.getState().verified,false);
  assert.equal(client.getState().reviewToken,'');
  mode=2;await client.refresh();
  assert.deepEqual(calls[2],{});
  assert.equal(client.getState().data.revision,'recovered');
  client.stop();
});

test('304 keeps the same data object and avoids resetting canvas state',async()=>{
  let calls=0;
  const client=createSnapshotClient({fetch:async()=>++calls===1?response(snapshot('same')):{status:304}});
  await client.refresh();const existing=client.getState().data;
  await client.refresh();
  assert.equal(client.getState().data,existing);
  assert.equal(client.getState().verified,true);
  client.stop();
});

test('concurrent refreshes serialize one follow-up so changes during a request are not lost',async()=>{
  const first=deferred();let calls=0,active=0,maxActive=0;
  const client=createSnapshotClient({fetch:async()=>{
    calls++;active++;maxActive=Math.max(active,maxActive);
    const result=calls===1?await first.promise:response(snapshot('after-write'));
    active--;return result;
  }});
  const loading=client.refresh();
  assert.equal(client.refresh(),loading);
  assert.equal(client.refresh(),loading);
  first.resolve(response(snapshot('before-write')));
  await loading;
  assert.equal(calls,2);assert.equal(maxActive,1);
  assert.equal(client.getState().data.revision,'after-write');
  client.stop();
});

test('corrupt, expired, oversized and unavailable browser caches do not block loading',async()=>{
  for(const raw of ['{broken',JSON.stringify({version:1,savedAt:0,data:snapshot('old')}),
    JSON.stringify({version:1,savedAt:100000000,data:{projects:[{}]}}),'x'.repeat(4*1024*1024+1)]){
    const client=createSnapshotClient({storage:{getItem:()=>raw},now:()=>100000000,
      fetch:async()=>response(snapshot('live'))});
    assert.equal(client.getState().data,null);
    await client.refresh();assert.equal(client.getState().data.revision,'live');client.stop();
  }
  const client=createSnapshotClient({storage:{getItem(){throw Error('denied');},setItem(){throw Error('quota');}},
    fetch:async()=>response(snapshot('live'))});
  await client.refresh();await wait();assert.equal(client.getState().verified,true);client.stop();
});

test('a stalled update times out while keeping cached canvas, then remains retryable',async()=>{
  let mode=0;
  const client=createSnapshotClient({storage:storageFor(),now:()=>2000,timeoutMs:10,
    fetch:(_,options)=>mode===1?Promise.resolve(response(snapshot('recovered'))):new Promise((_,reject)=>{
      options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})));
    })});
  await client.refresh();assert.equal(client.getState().data.revision,'cached');
  assert.match(client.getState().error,/超时/);assert.equal(client.getState().updating,false);
  mode=1;await client.refresh();assert.equal(client.getState().data.revision,'recovered');client.stop();
});

test('stopping a pending client prevents late results from replacing visible data',async()=>{
  const pending=deferred();const client=createSnapshotClient({fetch:()=>pending.promise});
  const loading=client.refresh();client.stop();pending.resolve(response(snapshot('late')));
  await loading;assert.equal(client.getState().data,null);
});
