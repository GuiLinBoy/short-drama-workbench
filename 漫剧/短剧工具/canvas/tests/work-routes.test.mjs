import test from 'node:test';
import assert from 'node:assert/strict';
import {parseWorkRoute,workRoute,projectState} from '../src/work-routes.js';

test('plain entry opens the library and never chooses an arbitrary project',()=>{
  for (const hash of ['', '#', '#/', '#/library'])
    assert.deepEqual(parseWorkRoute(hash),{view:'library',projectKey:''});
  assert.equal(workRoute('creative'),'#/library');
});
test('each project canvas has a stable URL; other project views retain their identity',()=>{
  for(const key of ['714340b819b0cfb2e4c18d6a','another-work']){
    assert.equal(workRoute('creative',key),`#/works/${key}/canvas`);
    for (const view of ['creative','reviews','assets','tasks','pipeline','clips','timeline'])
      assert.deepEqual(parseWorkRoute(workRoute(view,key)),{view,projectKey:key});
  }
});
test('bad links return to the library without resolving a different project',()=>{
  for (const hash of ['#/works/one','#/works/%ZZ/canvas','#/works/..%2Fsecret/canvas',
    '#/works/one/unknown','#/works/one/canvas/extra','#/unknown']) {
    const route=parseWorkRoute(hash);
    assert.equal(route.view,'library');assert.equal(route.projectKey,'');assert.ok(route.error);
  }
});
test('project lifecycle is explicit and never inferred from generation or approval counts',()=>{
  assert.equal(projectState({counts:{clip_approved:8,clips:8},finals:[{approved:true}]}),'in_progress');
  assert.equal(projectState({library_status:'completed'}),'completed');
  assert.equal(projectState({library_status:'archived'}),'archived');
  assert.equal(projectState({library_status:'invalid'}),'in_progress');
});
