import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import {projectLedger} from '../src/project-data.js';

const compiled=buildSync({entryPoints:[fileURLToPath(new URL('../src/workspace-navigation.jsx',import.meta.url))],
  bundle:true,write:false,platform:'node',format:'cjs',packages:'external'}).outputFiles[0].text;
const mod={exports:{}};
new Function('require','module','exports',compiled)(createRequire(import.meta.url),mod,mod.exports);
const Navigation=mod.exports.default,HomeBrand=mod.exports.HomeBrand;
const Icon=()=>null;

test('the library entrance has branding but no production sidebar until a project is present',()=>{
  assert.equal(renderToStaticMarkup(React.createElement(Navigation,{project:null,Icon})), '');
  const home=renderToStaticMarkup(React.createElement(HomeBrand,{Icon}));
  assert.match(home,/作品库首页/);assert.match(home,/#\/library/);
  assert.doesNotMatch(home,/<aside|创作画布|任务与花费/);
});

test('every production navigation link retains the selected project and home remains separate',()=>{
  for(const key of ['work-a','work-b']){
    const html=renderToStaticMarkup(React.createElement(Navigation,{project:{key,title:'当前作品'},view:'assets',Icon}));
    for(const view of ['canvas','assets','tasks','pipeline','timeline'])assert.match(html,new RegExp(`#/works/${key}/${view}`));
    assert.match(html,/aria-current="page"/);
    assert.match(html,/返回作品库/);
    assert.doesNotMatch(html,/所有作品|请先|>作品库<|work-[ab]\/library/);
  }
});

test('project ledger excludes other works, unknown entries and ambiguous duplicate titles',()=>{
  const first={key:'one',folder:'folder-one',title:'第一部'},second={key:'two',folder:'folder-two',title:'第二部'};
  const ledger=[{project:'folder-one',id:1},{project:'第一部',id:2},{project:'folder-two',id:3},{project:'第二部',id:4},{project:'',id:5}];
  assert.deepEqual(projectLedger({projects:[first,second],ledger},first).map(e=>e.id),[1,2]);
  assert.deepEqual(projectLedger({projects:[first,{...second,title:'第一部'}],ledger},first).map(e=>e.id),[1]);
});
