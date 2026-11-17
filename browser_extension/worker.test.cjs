const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function fixture(){
 let handler;let now=1000;const calls=[],timers=[];const data={running:true,config:{token:'fixture',delay:5},active:{task:{id:'one',name:'Synthetic'},tabId:12,startedAt:1000}};
 const context=vm.createContext({Date:{now:()=>now},setTimeout:f=>timers.push(f),fetch:async(url,options)=>{calls.push(['api',url,options]);return {ok:true,json:async()=>({resumed:data.allowResume!==false})};},chrome:{storage:{session:{get:async()=>structuredClone(data),set:async v=>Object.assign(data,v),remove:async key=>delete data[key]}},tabs:{reload:async id=>calls.push(['reload',id]),update:async(id,arg)=>calls.push(['navigate',id,arg])},alarms:{create:()=>{},onAlarm:{addListener:()=>{}}},runtime:{onMessage:{addListener:f=>handler=f}}}});
 vm.runInContext(fs.readFileSync(__dirname+'/worker.js','utf8'),context);
 return {send:m=>new Promise(resolve=>handler(m,{tab:{id:12},url:"https://connectonline.asic.gov.au/RegistrySearch/faces/landing/SearchRegisters.jspx"},resolve)),data,calls,timers,context,time:v=>now=v,run:code=>vm.runInContext(code,context)};
}
test('blank recovery reloads once then uses search entry, without resetting deadline',async()=>{
 const f=fixture();f.time(14000);await f.run("state().then(s=>recover(s,'blank'))");
 assert.equal(f.calls[0][0],'reload');assert.equal(f.data.active.startedAt,1000);
 f.time(28000);await f.run("state().then(s=>recover(s,'blank'))");assert.equal(f.calls[1][0],'navigate');assert.match(f.calls[1][2].url,/SearchRegisters.jspx$/);
 const result=await f.run("state().then(s=>recover(s,'blank'))");assert.match(result.error,/上限/);assert.equal(f.calls.length,2);
});
test('watchdog continues polling while a task is active',async()=>{const f=fixture();await f.run('tick()');assert.equal(f.timers.length,1);assert.ok(f.data.active);});
test('hard deadline records error, clears task and schedules next',async()=>{const f=fixture();f.time(182000);await f.run('tick()');assert.equal(f.data.active,undefined);assert.match(f.calls[0][1],/browser\/result$/);assert.equal(f.timers.length,1);});
test('watchdog makes one screenshot attempt before deadline and preserves unavailable reason',async()=>{
 const f=fixture();let captures=0;f.context.chrome.tabs.get=async()=>({active:false});f.context.chrome.tabs.captureVisibleTab=async()=>{captures++;};
 f.time(142000);await f.run('tick()');assert.equal(f.data.active.finalOcr,true);assert.match(f.data.active.lastError,/截图不可用/);
 await f.run('tick()');assert.equal(captures,0);f.time(182000);await f.run('tick()');
 assert.match(JSON.parse(f.calls[0][2].body).error,/截图不可用/);
});
test('paused worker performs no recovery or polling',async()=>{const f=fixture();f.data.running=false;await f.run('tick()');assert.equal(f.calls.length,0);assert.equal(f.timers.length,0);});
test('verification pause continues observation and resumes only its own pause',async()=>{
 const f=fixture();f.time(5000);await f.send({action:'blocked',id:'one'});
 assert.equal(f.data.running,false);assert.equal((await f.send({action:'task',id:'one'})).monitorOnly,true);
 f.time(15000);await f.send({action:'verification-cleared',id:'one'});
 assert.equal(f.data.running,true);assert.equal(f.data.active.startedAt,11000);assert.equal(f.data.active.verificationResumes,1);
});
test('manual backend pause cannot be overridden by delayed clear message',async()=>{
 const f=fixture();await f.send({action:'blocked',id:'one'});f.data.allowResume=false;
 await f.send({action:'verification-cleared',id:'one'});assert.equal(f.data.running,false);assert.equal(f.data.pauseReason,'manual');
 assert.equal((await f.send({action:'task',id:'one'})).task,null);
});
