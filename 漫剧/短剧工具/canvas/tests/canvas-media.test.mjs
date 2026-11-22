import test from 'node:test';
import assert from 'node:assert/strict';
import {observeCanvasMedia,mediaVersion} from '../src/deferred-media.js';
import {createMediaQueue} from '../src/media-queue.js';
import {readHiddenNodes,withoutHiddenNodes} from '../src/hidden-nodes.js';

test('offscreen nodes do not activate media; visible nodes activate once within the canvas root',()=>{
  const root={}, observers=[];
  class Observer {
    constructor(callback,options){this.callback=callback;this.options=options;this.targets=new Set();observers.push(this);}
    observe(target){this.targets.add(target);}
    unobserve(target){this.targets.delete(target);}
    disconnect(){this.targets.clear();this.disconnected=true;}
  }
  const first={closest:()=>root},second={closest:()=>root};
  const loaded=[];
  const stopFirst=observeCanvasMedia(first,()=>loaded.push('first'),Observer);
  const stopSecond=observeCanvasMedia(second,()=>loaded.push('second'),Observer);
  assert.equal(observers.length,1);
  const observer=observers[0];
  assert.equal(observer.options.root,root);
  assert.equal(observer.options.rootMargin,'120px');
  observer.callback([{target:first,isIntersecting:false},{target:second,isIntersecting:false}]);
  assert.deepEqual(loaded,[]);
  observer.callback([{target:first,isIntersecting:true}]);
  assert.deepEqual(loaded,['first']);
  observer.callback([{target:first,isIntersecting:false},{target:first,isIntersecting:true}]);
  assert.deepEqual(loaded,['first']);
  stopSecond();
  observer.callback([{target:second,isIntersecting:true}]);
  assert.deepEqual(loaded,['first']);
  stopFirst();assert.equal(observer.disconnected,true);
});

test('a changed resource gets a different lazy-load identity',()=>{
  const image={exists:true,url:'/api/media/id',size:123,modified:1,sha256:'old'};
  const old=mediaVersion(image);
  for (const change of [{size:124},{modified:2},{sha256:'new'},{url:'/api/media/new'}])
    assert.notEqual(mediaVersion({...image,...change}),old);
  assert.equal(mediaVersion({...image}),old);
  assert.equal(mediaVersion({exists:false}),null);
});

test('video decoding is limited to two concurrent jobs and releases queued or active cancellations',()=>{
  const schedule=createMediaQueue(2),started=[],finished={},released=[];
  const job=id=>done=>{started.push(id);finished[id]=done;return()=>released.push(id);};
  const stopA=schedule(job('a'));
  const stopB=schedule(job('b'));
  const stopC=schedule(job('c'));
  schedule(job('d'));
  assert.deepEqual(started,['a','b']);
  stopC();stopA();
  assert.deepEqual(started,['a','b','d']);
  assert.deepEqual(released,['a']);
  stopA();finished.d();stopB();
  assert.deepEqual(released,['a','d','b']);
});

test('removing canvas nodes drops only displayed nodes and their wires, with source records intact',()=>{
  const graph={nodes:[{id:'person'},{id:'empty-frame'},{id:'video'}],
    edges:[{source:'person',target:'empty-frame'},{source:'empty-frame',target:'video'},{source:'person',target:'video'}],
    currentReviewItems:[{id:'frame-prompt'}]};
  const before=structuredClone(graph);
  const hidden=withoutHiddenNodes(graph,['empty-frame']);
  assert.deepEqual(hidden.nodes.map(n=>n.id),['person','video']);
  assert.deepEqual(hidden.edges,[graph.edges[2]]);
  assert.deepEqual(graph,before);
  assert.deepEqual(hidden.currentReviewItems,graph.currentReviewItems);
  assert.deepEqual(withoutHiddenNodes(graph,[]),graph);
  assert.deepEqual(readHiddenNodes({getItem:()=>JSON.stringify(['empty-frame','empty-frame',4])},'work'),['empty-frame']);
  assert.deepEqual(readHiddenNodes({getItem:()=>'{broken'},'work'),[]);
  assert.deepEqual(readHiddenNodes({getItem(){throw Error('blocked');}},'work'),[]);
});
