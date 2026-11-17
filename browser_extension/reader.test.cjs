const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
async function run(text,assigned=true){
 const sent=[];const task={id:'task1',kind:'business',name:'SYNTHETIC CAFE'};
 const main={innerText:text,querySelectorAll:()=>[]};
 const context=vm.createContext({Date,Event:class{},setInterval:()=>{},document:{body:main,querySelector:s=>s==='main,[role="main"]'?main:null,querySelectorAll:()=>[]},chrome:{runtime:{onMessage:{addListener:()=>{}},sendMessage:async message=>{if(message.action==='task')return {task:assigned?task:null};sent.push(message);return {ok:true};}}}});
 vm.runInContext(fs.readFileSync(__dirname+'/reader.js','utf8'),context);
 await new Promise(resolve=>setImmediate(resolve));return sent;
}
test('exact assigned summary is returned',async()=>{const sent=await run('Business Name Summary\nBusiness name: SYNTHETIC CAFE\nRenewal date: 12/10/2029');assert.equal(sent.length,1);assert.equal(sent[0].action,'result');});
test('stale previous company is never returned as current result',async()=>{const sent=await run('Business Name Summary\nBusiness name: DIFFERENT CAFE\nRenewal date: 12/10/2029');assert.equal(sent.length,0);});
test('paused worker prevents page interaction and results',async()=>{assert.equal((await run('Business Name Summary\nBusiness name: SYNTHETIC CAFE',false)).length,0);});
test('verification challenge pauses without interaction',async()=>{const sent=await run('Verify you are human');assert.equal(sent[0].action,'blocked');});
