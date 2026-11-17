const BASE='http://127.0.0.1:8765';let ticking=false;
async function state(){return chrome.storage.session.get(['config','running','active','message','nextAt','tabId']);}
async function api(path,body){const {config}=await state();if(!config?.token)throw Error('请填写本机配对码');const response=await fetch(BASE+path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','X-Local-Token':config.token},...(body?{body:JSON.stringify(body)}:{})});const data=await response.json();if(!response.ok)throw Error(data.error||'本机接口失败');return data;}
async function setMessage(message){await chrome.storage.session.set({message});}
function later(ms=1500){setTimeout(()=>tick(),ms);}
async function recover(s,reason){
 const active=s.active;if(!active)return {ignored:true};
 if(Date.now()-active.startedAt>=180000||(active.recoveries||0)>=2)return {error:'自动恢复已达上限（最多两次／总计 180 秒）'};
 active.recoveries=(active.recoveries||0)+1;
 active.recoveryAt=Date.now();active.documentId=null;
 await chrome.storage.session.set({active,message:'正在自动恢复 '+active.task.name+'（'+active.recoveries+'/2）：'+reason});
 if(active.recoveries===1)await chrome.tabs.reload(active.tabId);
 else await chrome.tabs.update(active.tabId,{url:'https://connectonline.asic.gov.au/RegistrySearch/faces/landing/SearchRegisters.jspx',active:true});
 return {recovering:true};
}
async function tick(){if(ticking)return;ticking=true;try{const s=await state();if(!s.running)return;if(s.active){if(Date.now()-s.active.startedAt>180000){await api('/api/browser/result',{id:s.active.task.id,error:'页面等待超过 180 秒，已保留失败原因并继续下一项'});await chrome.storage.session.remove('active');later();}else later();return;}if(Date.now()<(s.nextAt||0)){later(Math.min(s.nextAt-Date.now(),20000));return;}
const {task,status}=await api('/api/browser/next');if(!task){await setMessage(status.message);if(status.state==='paused'){await chrome.storage.session.set({running:false});}else if(['done','idle'].includes(status.state)&&s.config.follow){later(2000);}else if(['done','stopped','error','idle'].includes(status.state))await chrome.storage.session.set({running:false});else later();return;}
let tabId=s.tabId;let tab;try{tab=tabId?await chrome.tabs.get(tabId):null;}catch{}if(!tab){tab=await chrome.tabs.create({url:'about:blank',active:true});tabId=tab.id;}
await chrome.storage.session.set({active:{task,tabId,startedAt:Date.now()},tabId,message:'正在查询 '+task.name});await chrome.tabs.update(tabId,{active:true,url:task.url||'https://connectonline.asic.gov.au/RegistrySearch/faces/landing/SearchRegisters.jspx'});later();
}catch(e){try{await api('/api/browser/pause',{message:'插件错误：'+e.message});}catch{}await chrome.storage.session.set({running:false,message:'已暂停：'+e.message});}finally{ticking=false;}}
chrome.alarms.create('registry-tick',{periodInMinutes:0.5});chrome.alarms.onAlarm.addListener(()=>tick());
chrome.runtime.onMessage.addListener((message,sender,reply)=>{(async()=>{
const s=await state();
if(sender.tab){if(sender.tab.id!==s.active?.tabId||!sender.url?.startsWith('https://connectonline.asic.gov.au/RegistrySearch/'))throw Error('不是当前查询标签页');
if(message.action==='task'){if(message.id&&message.id!==s.active.task.id)return {task:null};return {task:s.running?s.active.task:null};}
if(!s.running||message.id!==s.active.task.id)return {ignored:true};
if(message.action==='blocked'){await api('/api/browser/pause',{message:'ASIC 要求人工验证；插件已暂停'});await chrome.storage.session.set({running:false,message:'已暂停：ASIC 要求人工验证。请在该标签页处理后点击继续。'});return {ok:true};}
if(message.action==='recover')return recover(s,message.reason||'页面无响应');
if(message.action==='ocr'){
 const tab=await chrome.tabs.get(sender.tab.id);
 if(!tab.active||!await chrome.permissions.contains({permissions:['activeTab']}))return {error:'OCR 截图权限未启用：在 ASIC 页打开插件，点击启用本次截图 OCR 备用'};
 const screenshot=await chrome.tabs.captureVisibleTab(tab.windowId,{format:'png'});
 await api('/api/browser/result',{id:message.id,url:sender.url,screenshot});
 await chrome.storage.session.remove('active');await chrome.storage.session.set({nextAt:Date.now()+s.config.delay*1000,message:'已处理本机 OCR 备用结果'});later();return {ok:true};
}
if(message.action==='result'){await api('/api/browser/result',{id:message.id,url:sender.url,text:message.text||'',error:message.error||'',candidates:message.candidates||[],metrics:{...message.metrics,auto_recoveries:s.active.recoveries||0}});await chrome.storage.session.remove('active');await chrome.storage.session.set({nextAt:Date.now()+s.config.delay*1000,message:'已保存 '+s.active.task.name});later();return {ok:true};}
throw Error('不支持的页面消息');}
if(sender.id!==chrome.runtime.id)throw Error('不支持的消息来源');
if(message.action==='status')return {message:s.message,running:s.running};
if(message.action==='start'){const c=message.config;if(!c?.token||!['manual','reviewed','automation'].includes(c.scope)||![5,15,30,60].includes(c.delay))throw Error('请检查配对码与设置');if(s.running)throw Error('已有任务正在运行');await chrome.storage.session.set({config:c});if(!c.follow)await api('/api/browser/start',{scope:c.scope,refresh:!!c.refresh,include_company:!!c.include_company});await chrome.storage.session.remove('active');await chrome.storage.session.set({running:true,nextAt:0,message:'正在准备查询队列'});later();return {message:'已开始，关闭弹窗不影响任务'};}
if(message.action==='pause'){await api('/api/browser/pause',{message:'插件已暂停；已保存结果保留'});await chrome.storage.session.set({running:false,message:'已暂停；已保存结果保留'});return {message:'已暂停'};}
if(message.action==='resume'){if(message.config?.token)await chrome.storage.session.set({config:message.config});await api('/api/browser/resume',{});if(s.active){s.active.startedAt=Date.now();await chrome.storage.session.set({active:s.active});}await chrome.storage.session.set({running:true});if(s.active){await chrome.tabs.sendMessage(s.active.tabId,{action:'resume'});}later();return {message:'正在继续'};}
throw Error('未知操作');
})().then(reply).catch(e=>reply({error:e.message}));return true;});
