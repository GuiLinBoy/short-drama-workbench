import test from 'node:test';
import assert from 'node:assert/strict';
import { buildCreativeGraph, applySavedLayout, relatedNodes } from '../src/canvas-model.js';

function project() {
  return { key: 'local', title: '本地验收', assets: [
    { id: 'C1', name: '角色', type: 'character', status: 'approved' },
    { id: 'S1', name: '场景', type: 'scene', status: 'generated' },
    { id: 'UNUSED', name: '未引用', type: 'prop', status: 'planned' }
  ], clips: [
    { id: 'E1/1', episode: 'E1', assets: ['C1', 'S1'], status: 'planned', narration: [] },
    { id: 'E1/2', episode: 'E1', assets: ['C1'], status: 'generated', continue_from: 'E1/1',
      narration: [{ id: 'N1', text: '本地旁白', start: 2, duration: 3, audio: null }] },
    { id: 'E2/1', episode: 'E2', assets: ['C1'], status: 'planned', narration: [] }
  ], finals: [] };
}

test('embedded props reusing a visible approved image do not duplicate cards', () => {
  const input=project();
  input.assets[0].image={id:'shared-character-image',exists:true};
  input.assets.push({id:'P1',type:'prop',name:'内含手包',image:{id:'shared-character-image',exists:true}},
    {id:'P2',type:'prop',name:'独立道具',image:{id:'own-prop-image',exists:true}});
  input.clips[0].assets.push('P1','P2');
  input.creative_internal_assets=['P1','P2'];
  const graph=buildCreativeGraph(input);
  assert.ok(graph.nodes.some(n=>n.id==='asset:C1'));
  assert.ok(!graph.nodes.some(n=>n.id==='asset:P1'));
  assert.ok(graph.nodes.some(n=>n.id==='asset:P2'));
  const ids=new Set(graph.nodes.map(n=>n.id));
  assert.ok(graph.edges.every(e=>ids.has(e.source)&&ids.has(e.target)));
});
test('one shared character feeds videos directly without duplicate assets or storyboard nodes', () => {
  const graph = buildCreativeGraph(project(), { episode: 'E1' });
  assert.equal(graph.nodes.filter(node => node.id === 'asset:C1').length, 1);
  assert.equal(graph.edges.filter(edge => edge.source === 'asset:C1' && edge.relation === 'reference').length, 2);
  assert.ok(graph.edges.some(edge => edge.source === 'video:E1/1' && edge.target === 'video:E1/2' && edge.relation === 'continuity'));
  assert.ok(!graph.nodes.some(node => node.id === 'asset:UNUSED'));
  assert.ok(!graph.nodes.some(node => node.data.kind === 'story'));
  assert.ok(graph.edges.some(edge => edge.source === 'asset:C1' && edge.target === 'video:E1/1'));
  assert.ok(graph.nodes.find(node=>node.id==='video:E1/1').data.storyboardRecords.length);
});
test('episode and focus projection include immediate continuity context, never other episodes', () => {
  const input = project();
  input.clips.push({ id: 'E1/unrelated', episode: 'E1', assets: [], narration: [] });
  const graph = buildCreativeGraph(input, { episode: 'E1', focus: 'E1/2' });
  assert.deepEqual(graph.clips.map(clip => clip.id), ['E1/1', 'E1/2']);
  assert.ok(!graph.nodes.some(node => node.id.includes('E2')));
});
test('derived asset keeps its upstream form even when that parent is not directly used', () => {
  const input = project();
  input.assets.push({ id: 'C1-B', source_asset_id: 'C1', name: '衍生形态', type: 'character' });
  input.clips[0].assets = ['C1-B'];
  const graph = buildCreativeGraph(input, { episode: 'E1', focus: 'E1/1' });
  assert.ok(graph.edges.some(edge => edge.source === 'asset:C1' && edge.target === 'asset:C1-B'));
  assert.ok(buildCreativeGraph(input, { unused: true }).nodes.some(node => node.id === 'asset:UNUSED'));
});
test('missing references are warnings and no edge points at a nonexistent node', () => {
  const input = project();
  input.clips[0].assets.push('MISSING');
  input.clips[0].continue_from = 'E0/missing';
  const graph = buildCreativeGraph(input);
  assert.equal(graph.warnings.length, 2);
  const ids = new Set(graph.nodes.map(node => node.id));
  assert.ok(graph.edges.every(edge => ids.has(edge.source) && ids.has(edge.target)));
});
test('absent finals are not invented and missing audio remains missing', () => {
  const graph = buildCreativeGraph(project(), { episode: 'E1' });
  assert.equal(graph.nodes.find(node => node.id === 'final:E1'), undefined);
  const audio = graph.nodes.find(node => node.data.kind === 'audio');
  assert.equal(audio.data.record.audio, null);
  const input = project();
  input.finals = [{ episode: 'E1', video: { exists: false } }];
  const withReport = buildCreativeGraph(input, { episode: 'E1' });
  assert.ok(withReport.nodes.find(node => node.id === 'final:E1').data.planned);
  assert.ok(withReport.edges.some(edge => edge.source === audio.id && edge.target === 'final:E1'));
});
test('cyclic continuity is explicitly reported without changing the source records', () => {
  const input = project();
  input.clips[0].continue_from = 'E1/2';
  const graph = buildCreativeGraph(input, { episode: 'E1' });
  assert.ok(graph.warnings.some(warning => warning.includes('循环承接')));
  assert.equal(graph.nodes.filter(node => node.data.kind === 'video').length, 2);
});
test('narration from different clips occupies separate positions in the full canvas', () => {
  const input = project();
  input.clips[0].narration = [{ id: 'N0', text: '另一段旁白' }];
  const audioNodes = buildCreativeGraph(input, { episode: 'E1' }).nodes.filter(node => node.data.kind === 'audio');
  assert.equal(audioNodes.length, 2);
  assert.equal(new Set(audioNodes.map(node => `${node.position.x},${node.position.y}`)).size, 2);
});
test('relationship highlighting terminates on cycles and does not include unrelated siblings', () => {
  const edges = [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' },
    { source: 'c', target: 'b' }, { source: 'a', target: 'sibling' }];
  assert.deepEqual([...relatedNodes(edges, 'b')].sort(), ['a', 'b', 'c']);
});
test('saved browser positions accept only finite bounded coordinates for current nodes', () => {
  const nodes = [{ id: 'a', position: { x: 1, y: 2 } }, { id: 'b', position: { x: 3, y: 4 } }];
  const changed = applySavedLayout(nodes, { a: { x: 20, y: 30 }, b: { x: Infinity, y: 5 }, stale: { x: 8, y: 9 } });
  assert.deepEqual(changed.map(node => node.position), [{ x: 20, y: 30 }, { x: 3, y: 4 }]);
  assert.equal(changed.length, 2);
  assert.deepEqual(nodes[0].position, { x: 1, y: 2 });
});
test('building, highlighting and applying layout do not mutate project manifests', () => {
  const input = project(), before = structuredClone(input);
  const graph = buildCreativeGraph(input);
  relatedNodes(graph.edges, 'video:E1/1');
  applySavedLayout(graph.nodes, {});
  assert.deepEqual(input, before);
});


test('paper storyboards remain project detail records without standalone nodes or invented videos', () => {
  const input = project();
  input.clips = [];
  input.assets.push({ id: 'K1', name: '开门构图', type: 'scene', status: 'planned', source_asset_id: 'S1' });
  input.storyboards = [{ id: 'E1/C01', episode: 'E1', paper: true, duration: 3,
    assets: ['C1', 'S1', 'K1'], shots: [{ start: 0, end: 3, visual: '开门借钱' }] }];
  input.creative_asset_references = { K1: ['C1', 'S1'] };
  const original = JSON.stringify(input);
  const graph = buildCreativeGraph(input);
  assert.equal(graph.nodes.filter(n => n.data.kind === 'story').length, 0);
  assert.equal(graph.nodes.find(n=>n.data.kind==='project').data.storyboardRecords[0].shots[0].visual,'开门借钱');
  assert.equal(graph.nodes.filter(n => n.data.kind === 'video').length, 0);
  assert.equal(graph.nodes.filter(n => n.data.kind === 'asset').length, 3);
  assert.equal(graph.clips.length, 0);
  assert.equal(graph.stories[0].duration, 3);
  assert.equal(new Set(graph.edges.map(e => e.id)).size, graph.edges.length);
  assert.ok(graph.edges.some(e => e.source === 'asset:C1' && e.target === 'asset:K1'));
  assert.equal(JSON.stringify(input), original);
});

test('paper storyboard focus and episodes do not create or include unrelated video segments', () => {
  const input = project(); input.clips = [];
  input.storyboards = [
    { id: 'E1/C01', episode: 'E1', assets: ['C1'], paper: true, duration: 3 },
    { id: 'E1/C02', episode: 'E1', assets: ['S1'], paper: true, duration: 3.6 },
    { id: 'E2/C01', episode: 'E2', assets: ['C1'], paper: true, duration: 5 }
  ];
  const graph = buildCreativeGraph(input, { episode: 'E1', focus: 'E1/C02' });
  assert.deepEqual(graph.stories.map(s => s.id), ['E1/C02']);
  assert.equal(graph.nodes.filter(n => n.data.kind === 'video').length, 0);
  assert.ok(!graph.nodes.some(n => n.id === 'asset:C1'));
});
