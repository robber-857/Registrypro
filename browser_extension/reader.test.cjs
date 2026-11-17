const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
async function run(text,assigned=true){
 const sent=[];const task={id:'task1',kind:'business',name:'SYNTHETIC CAFE'};
 const main={innerText:text,querySelectorAll:()=>[]};
 const context=vm.createContext({Date,Event:class{},setInterval:()=>{},document:{getElementById:()=>({textContent:""}),body:main,querySelector:s=>s==='main,[role="main"]'?main:null,querySelectorAll:()=>[]},chrome:{runtime:{onMessage:{addListener:()=>{}},sendMessage:async message=>{if(message.action==='task')return {task:assigned?task:null};sent.push(message);return {ok:true};}}}});
 vm.runInContext(fs.readFileSync(__dirname+'/reader.js','utf8'),context);
 await new Promise(resolve=>setImmediate(resolve));return sent;
}
test('exact assigned summary is returned',async()=>{const sent=await run('Business Name Summary\nBusiness name: SYNTHETIC CAFE\nRenewal date: 12/10/2029');assert.equal(sent.length,1);assert.equal(sent[0].action,'result');});
test('stale previous company is never returned as current result',async()=>{const sent=await run('Business Name Summary\nBusiness name: DIFFERENT CAFE\nRenewal date: 12/10/2029');assert.equal(sent.length,0);});
test('paused worker prevents page interaction and results',async()=>{assert.equal((await run('Business Name Summary\nBusiness name: SYNTHETIC CAFE',false)).length,0);});
test('verification challenge pauses without interaction',async()=>{const sent=await run('Verify you are human');assert.equal(sent[0].action,'blocked');});


test('unresponsive exact result is retried only three times',async()=>{
 let now=0,interval,clicks=0;const sent=[];
 const link={innerText:'SYNTHETIC CAFE',offsetWidth:10,click:()=>{clicks++;}};
 const main={innerText:'Search Results',querySelectorAll:()=>[link]};
 const context=vm.createContext({Date:{now:()=>now},Event:class{},setInterval:f=>{interval=f;},document:{getElementById:()=>({textContent:''}),body:main,querySelector:s=>s==='main,[role="main"]'?main:null,querySelectorAll:()=>[]},chrome:{runtime:{onMessage:{addListener:()=>{}},sendMessage:async m=>{if(m.action==='task')return {task:{id:'retry',name:'SYNTHETIC CAFE',kind:'business'}};sent.push(m);return {ok:true};}}}});
 vm.runInContext(fs.readFileSync(__dirname+'/reader.js','utf8'),context);
 await new Promise(r=>setImmediate(r));
 for(const elapsed of [9000,18000,27000]){now=elapsed;await interval();}
 assert.equal(clicks,3);assert.equal(sent.length,1);assert.match(sent[0].error,/三次/);
});
async function searchFixture(pages,expectedAbn=''){
 let now=0,interval,page=0;const sent=[],clicked=[];
 const make=(entry,index)=>({id:'table:'+index+':orgName',innerText:entry.name,offsetWidth:10,click:()=>clicked.push(entry),closest:()=>({innerText:entry.name+' '+entry.abn+' '+entry.status,querySelectorAll:()=>[{innerText:''},{innerText:entry.name},{innerText:entry.abn},{innerText:entry.status}]})});
 const main={innerText:'Search Results',querySelectorAll:()=>pages[page].map(make)};
 const next={get disabled(){return page===pages.length-1;},getAttribute:()=>null,click:()=>{page++;}};
 const context=vm.createContext({Date:{now:()=>now},Event:class{},setInterval:f=>{interval=f;},document:{body:main,querySelector:s=>s==='main,[role="main"]'?main:s.includes('pagingNextButton')?next:null,querySelectorAll:()=>[]},chrome:{runtime:{onMessage:{addListener:()=>{}},sendMessage:async m=>{if(m.action==='task')return {task:{id:'selection',name:'SYNTHETIC CAFE',kind:'business',abn:expectedAbn}};sent.push(m);return {ok:true};}}}});
 vm.runInContext(fs.readFileSync(__dirname+'/reader.js','utf8'),context);await new Promise(r=>setImmediate(r));
 for(let i=0;i<4&&!sent.length&&!clicked.length;i++){now+=2000;await interval();}
 return {sent,clicked,page};
}
test('cancelled names are excluded before choosing current expected ABN',async()=>{
 const r=await searchFixture([[{name:'SYNTHETIC CAFE',abn:'99 115 089 033',status:'Cancelled'},{name:'SYNTHETIC CAFE',abn:'51 642 681 114',status:'Registered'}]],'51642681114');
 assert.equal(r.clicked.length,1);assert.equal(r.clicked[0].abn,'51 642 681 114');
});
test('expected ABN on second page is found',async()=>{
 const r=await searchFixture([[{name:'SYNTHETIC CAFE',abn:'51 642 681 114',status:'Registered'}],[{name:'SYNTHETIC CAFE',abn:'99 115 089 033',status:'Registered'}]],'99115089033');
 assert.equal(r.page,1);assert.equal(r.clicked.length,1);assert.equal(r.clicked[0].abn,'99 115 089 033');
});
test('multiple current entities without expected ABN remain ambiguous',async()=>{
 const r=await searchFixture([[{name:'SYNTHETIC CAFE',abn:'51 642 681 114',status:'Registered'},{name:'SYNTHETIC CAFE',abn:'99 115 089 033',status:'Registered'}]]);
 assert.equal(r.clicked.length,0);assert.match(r.sent[0].error,/歧义/);
});
