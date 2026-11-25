const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync('app/static/app.js','utf8');
const code=src.slice(src.indexOf("$('clear').onclick"),src.indexOf("$('length').onchange"));
const nodes=new Map(),commands=[],timers=[];let confirmed=false,blob,clicked=false;
const get=id=>{if(!nodes.has(id))nodes.set(id,{children:[],querySelectorAll(){return this.children}});return nodes.get(id)};
get('transcript-interviewer').children=[{textContent:'Question one'},{textContent:'Question two'}];
get('transcript-me').children=[{textContent:'My response'}];get('answers').children=[{innerText:'Suggested response'}];
const link={click(){clicked=true},remove(){}};
const full={metadata:{company:'Sample'},history:Array.from({length:150},(_,n)=>({role:'interviewer',text:n===0?'Question one':n===1?'Question two':'Question '+n})),partials:[],answers:[{question:'Prompt',text:'Suggested response'}],analysis:{coverage:'full'}};
full.history.push({role:'me',text:'My response'});
const env={currentReviewId:'a'.repeat(32),fetch:async()=>({ok:true,json:async()=>full}),encodeURIComponent,error:message=>{throw Error(message)},$:get,window:{confirm(){return confirmed}},command:x=>commands.push(x),Blob,Date,Array,
 URL:{createObjectURL(x){blob=x;return 'blob:review'},revokeObjectURL(){}},
 document:{createElement:()=>link,body:{append(){}}},setTimeout:fn=>timers.push(fn)};
vm.createContext(env);vm.runInContext(code,env);
get('clear').onclick();assert.equal(commands.length,0);
confirmed=true;get('clear').onclick();assert.equal(commands[0].cmd,'clear');
(async()=>{
 await get('download-review').onclick();assert(clicked);assert(link.download.endsWith('.txt'));
 const text=await blob.text();assert(text.includes('Question one'));assert(text.includes('Question 149'));assert(text.includes('My response'));assert(text.includes('Suggested response'));
 console.log('Review controls passed: cancel/confirm clear and full-session download beyond the live window.');
})().catch(e=>{console.error(e);process.exitCode=1});
