const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync('app/static/app.js', 'utf8');
const refresh = source.slice(source.indexOf('async function refresh()'), source.indexOf('function liveOptions()'));
async function check(previous, devices, expected) {
  function select(value) { return {value,options:[],replaceChildren(...items){this.options=items;this.value=items[0].value},add(item){this.options.push(item)}}; }
  const fields={mic:select(previous),system:select('')};
  const env={fetch:async()=>({json:async()=>({devices})}),$:id=>fields[id],buttons(){},error(message){throw new Error(message)},Option:function(text,value){return {text,value}}};
  vm.createContext(env);vm.runInContext(refresh,env);await env.refresh();
  assert.equal(fields.mic.value,expected);assert.equal(fields.system.value,'1');
}
(async()=>{
 const devices=[{id:0,name:'iPhone (2) Microphone',channels:1},{id:1,name:'IdeaShare 2ch',channels:2},{id:2,name:'MacBook Pro Microphone',channels:1}];
 await check('',devices,'2');
 await check('0',devices,'0');
 await check('',devices.filter(x=>x.id!==2),'0');
 console.log('Microphone selection passed: MacBook Pro preferred over iPhone, explicit selections preserved, fallback retained.');
})().catch(error=>{console.error(error);process.exitCode=1});
