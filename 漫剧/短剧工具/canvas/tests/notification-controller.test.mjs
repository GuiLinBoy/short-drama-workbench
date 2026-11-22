import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {canSubmitReviewBatch} from '../src/review-model.js';

const source=fs.readFileSync(new URL('../../tools/canvas_notify_controller.js',import.meta.url),'utf8');
const run=vm.runInNewContext('('+source+')');
const id='a'.repeat(32);
const route={threadId:'SYNTHETIC_TEST_DIRECTOR',hostId:'SYNTHETIC_TEST_HOST',simulation:false};
const config={project:'D:/SYNTHETIC-ONLY',toolRoot:'D:/SYNTHETIC-ONLY/tools-root',owner:'TEST-controller',timeoutSeconds:0};
function fixture({uncertain=false,pending=false,simulation=false}={}) {
  const calls=[];
  const tools={
    exec_command:async args=>{calls.push(['command',args.cmd]);let value={};
      if(args.cmd.includes("'pending-receipts'"))value={items:pending?[{notificationId:id,route}]:[]};
      else if(args.cmd.includes("'wait'"))value=pending?{state:'idle'}:{state:'claimed',notificationId:id,claimId:'TEST-claim',route:{...route,simulation},prompt:'SYNTHETIC review-only notification'};
      else if(args.cmd.includes("'delivery'"))value={state:uncertain?'delivery_unknown':'delivered'};
      else if(args.cmd.includes("'receipt'"))value={state:uncertain?'awaiting_receipt':'received'};
      else if(args.cmd.includes("'stop'"))value={online:false};
      return {exit_code:0,output:JSON.stringify(value)};
    },
    mcp__codex_app__send_message_to_thread:async args=>{calls.push(['send',args]);if(uncertain)throw new Error('synthetic lost response');return {structuredContent:{threadId:route.threadId,turnId:'TEST-turn',status:'queued'}};},
    mcp__codex_app__read_thread:async args=>{calls.push(['read',args]);return {schemaVersion:1,thread:{id:route.threadId},turns:[]};},
    mcp__codex_app__wait_threads:async args=>{calls.push(['wait-app',args]);return {};}
  };return {tools,calls};
}
test('batch notification rejects unsaved edits and selections',()=>{
  const base={notifications:{canNotify:true},note:'',selectionCount:0,busy:false,writable:true};
  assert.equal(canSubmitReviewBatch(base),true);
  for(const changed of [{note:'尚未保存的意见'},{selectionCount:1},{busy:true},{writable:false},{notifications:{canNotify:false}}])
    assert.equal(canSubmitReviewBatch({...base,...changed}),false);
});
test('active controller sends once through native chat tool, then verifies receipt and stops listening',async()=>{
  const {tools,calls}=fixture();const result=await run(tools,config);
  assert.equal(result.receipt,'received');assert.equal(calls.filter(c=>c[0]==='send').length,1);
  assert.equal(calls.filter(c=>c[0]==='read').length,1);
  assert.ok(calls.at(-1)[1].includes("'stop'"));
  const command=calls.find(c=>c[0]==='command'&&c[1].includes('v=json.loads'))[1];
  assert.ok(command.startsWith("@'\n"));assert.ok(command.includes('p.open("x"'));
});
test('ambiguous send response is never resent automatically',async()=>{
  const {tools,calls}=fixture({uncertain:true});const result=await run(tools,config);
  assert.equal(result.state,'delivery_unknown');assert.equal(calls.filter(c=>c[0]==='send').length,1);
});
test('restarting controller reconciles prior original thread without another send',async()=>{
  const {tools,calls}=fixture({pending:true});const result=await run(tools,config);
  assert.equal(result.state,'idle');assert.equal(calls.filter(c=>c[0]==='send').length,0);
  assert.equal(calls.filter(c=>c[0]==='read').length,1);
});
test('synthetic route is never delivered into a real chat',async()=>{
  const {tools,calls}=fixture({simulation:true});await assert.rejects(()=>run(tools,config),/Synthetic route/);
  assert.equal(calls.filter(c=>c[0]==='send').length,0);
  assert.ok(calls.at(-1)[1].includes("'stop'"));
});
