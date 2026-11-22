import test from 'node:test';
import assert from 'node:assert/strict';
import { arrangeCanvas, nodeDimensions, layoutSnapshot } from '../src/canvas-layout.js';
import { applySavedLayout } from '../src/canvas-model.js';
import {presentCanvasGraph,visibleCanvasEdges} from '../src/canvas-presentation.js';
import {readTheme} from '../src/theme.js';
import {inspectorWidth,initialCanvasZoom} from '../src/responsive-layout.js';

const node = (id, kind, height = 200) => ({ id, type: 'creative', data: { kind },
  measured: { width: 240, height }, position: { x: -25, y: -25 } });
const edge = (source, target, relation = 'production') => ({ source, target, relation });
function assertNoOverlap(nodes) {
  nodes.forEach((a, i) => nodes.slice(i + 1).forEach(b => {
    const da = nodeDimensions(a), db = nodeDimensions(b);
    assert.ok(a.position.x + da.width <= b.position.x || b.position.x + db.width <= a.position.x ||
      a.position.y + da.height <= b.position.y || b.position.y + db.height <= a.position.y,
      `${a.id} overlaps ${b.id}`);
  }));
}
test('measured mixed-size cards are separated and production categories occupy aligned columns', () => {
  const nodes = [node('work', 'project', 130), node('role', 'asset', 180), node('scene', 'asset', 350),
    node('story1', 'story', 280), node('story2', 'story', 220), node('v1', 'video'), node('v2', 'video'),
    node('speech', 'audio', 500), node('final', 'final')];
  const edges = [edge('work', 'story1'), edge('role', 'story1'), edge('scene', 'story2'),
    edge('story1', 'v1'), edge('story2', 'v2'), edge('story2', 'speech'),
    edge('v1', 'v2', 'continuity'), edge('v1', 'final'), edge('v2', 'final'), edge('speech', 'final')];
  const result = arrangeCanvas(nodes, edges), byId = new Map(result.map(n => [n.id, n]));
  assertNoOverlap(result);
  edges.filter(e => e.relation !== 'continuity').forEach(e => assert.ok(byId.get(e.source).position.x <= byId.get(e.target).position.x));
  assert.ok(byId.get('v1').position.y < byId.get('v2').position.y);
});
test('continuity order takes precedence over reversed names and input order', () => {
  const result = arrangeCanvas([node('a-last', 'video'), node('z-first', 'video')], [edge('z-first', 'a-last', 'continuity')]);
  assert.ok(result[1].position.y < result[0].position.y);
  assert.equal(result[0].position.x, result[1].position.x);
});
test('layout is deterministic regardless of current manual positions and array order', () => {
  const nodes = [node('a', 'asset'), node('b', 'story'), node('c', 'video')], edges = [edge('a', 'b'), edge('b', 'c')];
  const first = layoutSnapshot(arrangeCanvas(nodes, edges));
  const second = layoutSnapshot(arrangeCanvas([...nodes].reverse().map(n => ({ ...n, position: { x: 999, y: 999 } })), [...edges].reverse()));
  assert.deepEqual(first, second);
});
test('cycles and missing references do not hang, overlap, or discard nodes', () => {
  const nodes = [node('a', 'asset'), node('b', 'asset'), node('c', 'asset')];
  const result = arrangeCanvas(nodes, [edge('a', 'b'), edge('b', 'a'), edge('a', 'missing')]);
  assert.equal(result.length, nodes.length);
  assert.ok(result.every(n => Number.isFinite(n.position.x) && Number.isFinite(n.position.y)));
  assertNoOverlap(result);
});
test('arrange plus coordinate undo preserves current records and does not mutate inputs', () => {
  const nodes = [node('a', 'asset'), node('b', 'video')], edges = [edge('a', 'b')];
  const before = structuredClone({ nodes, edges }), snapshot = layoutSnapshot(nodes);
  const arranged = arrangeCanvas(nodes, edges);
  const updated = arranged.map(n => ({ ...n, data: { ...n.data, status: 'approved' } }));
  const restored = applySavedLayout(updated, snapshot);
  assert.deepEqual(layoutSnapshot(restored), snapshot);
  assert.ok(restored.every(n => n.data.status === 'approved'));
  assert.deepEqual({ nodes, edges }, before);
});
test('empty and unmeasured inputs have safe layouts', () => {
  assert.deepEqual(arrangeCanvas([], []), []);
  const nodes = [node('a', 'audio'), node('b', 'audio')];
  nodes[0].measured = { width: Infinity, height: -1 };
  delete nodes[1].measured;
  assertNoOverlap(arrangeCanvas(nodes, []));
});

test('large-screen inspector preferences leave working canvas space on laptop widths',()=>{
  for(const available of [900,1200,1440,2200]) {
    const width=inspectorWidth(900,available);
    assert.ok(width<=available*.4);
    assert.ok(available-width>=480);
    assert.ok(width>=260 && width<=900);
  }
  assert.equal(inspectorWidth(600,1200),480);
  assert.equal(inspectorWidth(600,2200),600);
  assert.equal(inspectorWidth(NaN,1200),360);
});

test('initial canvas zoom fits the primary columns while retaining bounded manual-scale defaults',()=>{
  assert.ok(initialCanvasZoom(948,568,1800)*1800<=948-40);
  assert.ok(initialCanvasZoom(606,440,1800)<initialCanvasZoom(1354,817,1800));
  assert.equal(initialCanvasZoom(2200,1200,1800),.85);
  assert.equal(initialCanvasZoom(300,250,1800),.4);
});

test('global theme remembers explicit selection, migrates canvas preference and tolerates unavailable storage',()=>{
  const storage=values=>({getItem:key=>values[key] ?? null});
  assert.equal(readTheme(storage({'manju.canvas.background':'dark'})),'dark');
  assert.equal(readTheme(storage({'manju.studio.theme':'light','manju.canvas.background':'dark'})),'light');
  assert.equal(readTheme(storage({'manju.studio.theme':'dark'})),'dark');
  assert.equal(readTheme(storage({})),'light');
  assert.equal(readTheme({getItem:()=>{throw new Error('storage disabled');}}),'light');
});

test('characters and props share one column, keyframes another and all videos another regardless of dependency depth', () => {
  const role=node('role','asset'),prop=node('prop','asset'),frame=node('frame','asset'),frame2=node('frame2','asset');
  role.data.record={type:'character',source_asset_id:'other-role'};
  prop.data.record={type:'prop'};
  frame.data.record={type:'scene',source_asset_id:'room'};
  frame2.data.record={type:'scene',name:'三人客厅构图参考'};
  const input=[role,prop,frame,frame2,node('v1','video'),node('v2','video')];
  const result=arrangeCanvas(input,[edge('role','prop'),edge('role','frame'),edge('frame','frame2'),edge('frame2','v1'),edge('v1','v2','review')]);
  const byId=new Map(result.map(n=>[n.id,n]));
  assert.equal(byId.get('role').position.x,byId.get('prop').position.x);
  assert.equal(byId.get('frame').position.x,byId.get('frame2').position.x);
  assert.equal(byId.get('v1').position.x,byId.get('v2').position.x);
  assert.ok(byId.get('role').position.x<byId.get('frame').position.x);
  assert.ok(byId.get('frame').position.x<byId.get('v1').position.x);
  assert.equal(new Set(result.map(n=>n.position.x)).size,3);
  assertNoOverlap(result);
});

test('clean canvas hides project wires and merges parallel relationships without losing review evidence',()=>{
  const graph={nodes:[node('work','project'),node('role','asset'),node('v1','video'),node('v2','video')],edges:[
    {...edge('work','v1'),id:'work'},
    {...edge('role','v1','reference'),id:'ref',label:'素材引用'},
    {...edge('role','v1','review'),id:'review',reviewId:'old-image',historical:true},
    {...edge('role','v2','reference'),id:'other'}],reviewNodeById:{plan:'work'}};
  const before=structuredClone(graph),clean=presentCanvasGraph(graph);
  assert.ok(!clean.nodes.some(n=>n.id==='work'));
  assert.equal(clean.edges.length,2);
  assert.equal(clean.edges[0].links.length,2);
  assert.equal(clean.edges[0].links[1].reviewId,'old-image');
  assert.match(clean.edges[0].label,/历史版本/);
  assert.equal(visibleCanvasEdges(clean.edges,'v1').length,1);
  assert.equal(visibleCanvasEdges(clean.edges,'role').length,2);
  assert.equal(visibleCanvasEdges(clean.edges,'v1',true).length,2);
  assert.deepEqual(graph,before);
});
