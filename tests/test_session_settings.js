const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('app/static/app.js','utf8');
const part=source.slice(source.indexOf('function liveOptions()'),source.indexOf('for (const id of safetyFields) {',source.indexOf('async function refreshQuote()')));
function setup(fetcher){
 const timers=new Map();let counter=0;
 const fields={ 'live-minutes':{value:'60',max:'120',get valueAsNumber(){return Number(this.value)}},keyterms:{checked:false},'live-quote':{},'live-blockers':{replaceChildren(...x){this.items=x}},limit:{}};
 const env={AbortController,fetch:fetcher,document:{createElement:()=>({})},$:id=>fields[id],buttons(){},currentState:'idle',quoteData:null,quoteTimer:null,quoteRequest:0,quoteFailures:0,
 setTimeout(fn,ms){const id=++counter;timers.set(id,{fn,ms});return id},clearTimeout(id){timers.delete(id)}};
 vm.createContext(env);vm.runInContext(part,env);return {env,fields,timers};
}
const ok=()=>({ok:true,status:200,json:async()=>({quote_id:'local-only-test',minutes:60,maximum_payment_ugx:0,blockers:[],allowance_status:'Local settings ready'})});
(async()=>{
 let calls=0;const a=setup(async()=>{calls++;return ok()});a.fields['live-minutes'].value='3';await a.env.refreshQuote();
 assert.equal(calls,0);assert(a.fields['live-blockers'].items[0].textContent.includes('5 to 120'));assert(a.fields['live-blockers'].items[0].textContent.includes('current value is 3'));
 a.fields['live-minutes'].value='60';await a.env.refreshQuote();assert.equal(a.env.quoteData.quote_id,'local-only-test');assert.equal(calls,1);
 let failure=true;const b=setup(async()=>{if(failure)throw new TypeError('Network failed');return ok()});await b.env.refreshQuote();
 assert(b.fields['live-blockers'].items[0].textContent.includes('Reconnecting'));assert([...b.timers.values()].some(x=>x.ms===2000));
 failure=false;await [...b.timers.values()].find(x=>x.ms===2000).fn();assert.equal(b.env.quoteData.quote_id,'local-only-test');
 const c=setup(async()=>({ok:false,status:422,json:async()=>({detail:[{loc:['body','options','minutes'],msg:'Input should be greater than or equal to 5'}]})}));await c.env.refreshQuote();assert(c.fields['live-blockers'].items[0].textContent.includes('greater than or equal to 5'));
 let unavailable=true;const d=setup(async()=>unavailable?{ok:false,status:503,json:async()=>({})}:ok());await d.env.refreshQuote();assert(d.fields['live-blockers'].items[0].textContent.includes('HTTP 503'));
 unavailable=false;await [...d.timers.values()].find(x=>x.ms===2000).fn();assert.equal(d.env.quoteData.quote_id,'local-only-test');
 assert(source.includes("$('live-minutes').value = $('live-minutes').defaultValue || '60'"));
 console.log('Session-settings checks passed: invalid duration, valid recovery, specific backend validation, temporary network outage and slow/unavailable backend recovery.');
})().catch(e=>{console.error(e);process.exitCode=1});
