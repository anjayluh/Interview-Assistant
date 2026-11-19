// Execute the actual desktop connection handler with background timers disabled.
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const src=fs.readFileSync('app/static/app.js','utf8');
const connect=src.slice(src.indexOf('function connect()'),src.indexOf('async function refresh()'));
const nodes=new Map();const get=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',replaceChildren(){throw Error('Completed answers must remain visible')}});return nodes.get(id)};
let sent=[],intervals=0;
class Socket {static OPEN=1;constructor(){this.readyState=1}send(x){sent.push(JSON.parse(x))}}
const env={WebSocket:Socket,location:{host:'127.0.0.1:8765'},client:'test-client',socket:null,
 $:get,encodeURIComponent,connected:false,retry:0,heartbeat:null,timer:null,
 clearInterval(){},setInterval(){intervals++;return 1},buttons(){},refreshQuote(){},
 answerDraft:null};
vm.createContext(env);vm.runInContext(connect,env);env.connect();env.socket.onopen();
assert.equal(intervals,1); // Timer is registered but never executed in this test.
env.socket.onmessage({data:JSON.stringify({type:'heartbeat'})});
assert.equal(sent.length,1);assert.equal(sent[0].cmd,'heartbeat');
env.socket.onmessage({data:JSON.stringify({type:'answer_superseded',revision:2})});
console.log('Desktop connection passed: server heartbeat answered without interval timers, completed answers retained.');
