// Simulated DOM and transport acceptance check; not Safari or physical-device testing.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('app/static/companion.html', 'utf8');
const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map(x=>x[1]));
const nodes = new Map(), timers = [];
function element() { return {value:'',dataset:{},options:[],hidden:false,children:[],textContent:'',
  append(...items){for(const x of items){x.parent=this;this.children.push(x);}},
  prepend(x){x.parent=this;this.children.unshift(x)},
  replaceChildren(...items){this.children=[];this.textContent='';this.append(...items)},
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this)},
  addEventListener(){},click(){return this.onclick()},get lastChild(){return this.children.at(-1)}};}
function get(id) {assert(ids.has(id),'Missing real HTML element '+id);if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)}
let writer;
const transport = new ReadableStream({start(controller){writer=controller}});
const memory={iaDevice:'device-test'};
const environment={document:{body:{dataset:{companion:'true'}},getElementById:get,createElement:element},
  location:{hash:'',pathname:'/companion'},history:{replaceState(){}},navigator:{},
  sessionStorage:{getItem:k=>memory[k],setItem:(k,v)=>memory[k]=v,removeItem:k=>delete memory[k]},
  setTimeout:fn=>timers.push(fn),Option:function(){},performance,TextDecoder,
  requestAnimationFrame:fn=>fn(),
  fetch:async(path,options)=>{assert.equal(path,'/api/companion/events');assert.equal(options.headers.Authorization,'Bearer device-test');return {ok:true,body:transport}}};
const source=fs.readFileSync('app/static/copilot.js','utf8');
const settle=async()=>{for(let i=0;i<10;i++)await new Promise(r=>setImmediate(r))};
const send=x=>writer.enqueue(new TextEncoder().encode(JSON.stringify(x)+'\n'));
const allText=node=>node.textContent+' '+node.children.map(allText).join(' ');
(async()=>{
 vm.runInNewContext(source,environment);
 send({type:'companion_snapshot',state:'running',history:[],partials:[],answers:[],metrics:[]});await settle();
 send({type:'transcript',key:'i1',role:'interviewer',source:'system_audio',text:'Find duplicate values',final:false});await settle();
 assert(allText(get('phone-interviewer')).includes('Find duplicate values'));
 assert(get('phone-interviewer').children[0].className.includes('partial'));
 assert.equal(get('phone-answers').children.length,0);
 send({type:'groq',status:'generating'});await settle();assert(get('phone-answer-status').textContent.includes('Generating'));
 send({type:'transcript',key:'i1',role:'interviewer',source:'system_audio',text:'Find duplicates and preserve order.',final:true});
 send({type:'transcript',key:'m1',role:'me',source:'microphone',text:'I would clarify the input.',final:true});await settle();
 assert(allText(get('phone-me')).includes('I would clarify the input'));
 assert(!allText(get('phone-interviewer')).includes('I would clarify'));
 send({type:'transcript',key:'m2',role:'me',source:'microphone',text:'Find duplicates and preserve order.',final:false});
 send({type:'transcript',key:'m2',role:'me',source:'microphone',text:'Find duplicates and preserve order.',final:true,duplicate_of:'i1'});
 send({type:'answer_delta',text:"I'd track previously seen values.",revision:2});await settle();
 assert(!allText(get('phone-me')).includes('Find duplicates'));
 assert.equal(get('phone-draft').textContent,"I'd track previously seen values.");
 assert(get('phone-answer-status').textContent.includes('draft'));
 assert.equal(get('phone-answers').children.length,0);
 send({type:'answer',text:"I'd use a set and preserve traversal order.",question:'RAW INTERVIEWER PROMPT MUST NOT BE AN ANSWER',category:'Coding',code:'seen = set()',revision:2});await settle();
 const spoken=allText(get('phone-answers'));
 assert(spoken.includes("I'd use a set"));assert(!spoken.includes('RAW INTERVIEWER'));
 assert(!spoken.includes('I would clarify the input'));assert(get('phone-draft').hidden);
 send({type:'answer_superseded',revision:3});await settle();assert.equal(get('phone-answers').children.length,0);
 send({type:'companion_snapshot',state:'running',history:[{key:'i1',role:'interviewer',text:'Latest question',final:true},{key:'m1',role:'me',text:'My real response',final:true}],partials:[],answers:[],metrics:[]});await settle();
 assert(allText(get('phone-interviewer')).includes('Latest question'));assert(allText(get('phone-me')).includes('My real response'));
 const failed = {phase:'failed',message:'Groq rejected the request (HTTP 400).',diagnostic:{http_status:400,category:'invalid_request'}};
 send({type:'groq',status:'error',message:failed.message,diagnostic:failed.diagnostic});
 send({type:'clear'});
 send({type:'state',state:'idle',reason:'Stopped',answer_status:failed});await settle();
 assert(get('phone-answer-status').textContent.includes('HTTP 400'));
 assert(!get('phone-answer-status').textContent.includes('Waiting'));
 send({type:'companion_snapshot',state:'idle',history:[],partials:[],answers:[],metrics:[],answer_status:failed});await settle();
 assert(get('phone-answer-status').textContent.includes('HTTP 400'));
 assert(get('provider-error-diagnostic').textContent.includes('invalid_request'));
 send({type:'companion_snapshot',state:'running',history:[{key:'i1',role:'interviewer',text:'Latest question',final:true}],partials:[],answers:[],metrics:[],answer_status:{phase:'idle'}});await settle();
 writer.close();await settle();assert(get('phone-status').textContent.includes('Reconnecting'));
 assert(allText(get('phone-interviewer')).includes('Latest question'));
 console.log('Phone simulated-DOM acceptance passed: source separation, partials, duplicate removal, streaming draft, AI-only completed answer, supersession and reconnect snapshot.');
})().catch(error=>{console.error(error);process.exitCode=1});
