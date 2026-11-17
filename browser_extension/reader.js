// Reads only the assigned public ASIC tab. No payment, login or CAPTCHA interaction.
let task=null,finished=false,clicked=false,started=Date.now(),lookupStage=0,formWaits=0;
const norm=s=>String(s).toLowerCase().replace(/[^a-z0-9]/g,'');
const visible=e=>!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);
async function report(result){if(finished)return;finished=true;const response=await chrome.runtime.sendMessage({action:'result',id:task.id,...result});if(response.error){finished=false;}}
function lookup(){
 const select=document.querySelector('select[id$="templateSearchTypesListOfValuesId::content"]');
 if(!select)return false;
 if(lookupStage===0){const option=Array.from(select.options).find(o=>o.textContent.trim()===(task.kind==='business'?'Business names index':'Organisation and Business Names'));if(!option)return false;select.value=option.value;select.dispatchEvent(new Event('change',{bubbles:true}));lookupStage=1;return true;}
 const suffix=task.kind==='business'?'searchName::content':'templateSearchInputText::content';
 const input=document.querySelector('input[id$="'+suffix+'"]');if(!input||!visible(input)){if(++formWaits===4)document.querySelector('[id="bnConnectionTemplate:pt_s5:searchButtonId"]')?.click();return true;}
 if(lookupStage===1){input.value=task.kind==='company'&&task.acn?task.acn:task.name;input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.blur();lookupStage=2;return true;}
 if(lookupStage===2){document.querySelector('[id="bnConnectionTemplate:pt_s5:searchButtonId"]')?.click();lookupStage=3;started=Date.now();return true;}
 return false;
}
async function step(){try{const response=await chrome.runtime.sendMessage({action:'task'});if(!response.task)return;task=response.task;if(finished)return;
const main=document.querySelector('main,[role="main"]');const text=(main||document.body).innerText;
if(/verify you are human|captcha|unusual traffic|access denied/i.test(text)||Array.from(document.querySelectorAll('iframe[title]')).some(e=>visible(e)&&/recaptcha.*challenge|challenge.*recaptcha/i.test(e.title))){finished=true;await chrome.runtime.sendMessage({action:'blocked',id:task.id});return;}
const marker=task.kind==='business'?'Business Name Summary':'Company Summary';
if(lookupStage===1||lookupStage===2){lookup();return;}
if(text.includes(marker)){
 const label=task.kind==='business'?'Business name':'Name';const match=text.match(new RegExp('(?:^|\\n)\\s*'+label+'\\s*:\\s*([^\\n]+)','i'));
 if(match&&norm(match[1])===norm(task.name)){await report({text:text.slice(0,150000)});return;}
 if(Date.now()-started>4000&&lookupStage===0){lookup();return;}
 if(Date.now()-started>20000){await report({error:'摘要名称与指定任务不一致'});return;}
}
if(/0 results found|no results found/i.test(text)){await report({error:'官方名称搜索没有结果'});return;}
if(!clicked&&/Search Results/.test(text)){
 const links=Array.from((main||document).querySelectorAll('a')).filter(e=>visible(e)&&norm(e.innerText)===norm(task.name));
 if(links.length===1){clicked=true;links[0].click();return;}
 if(links.length>1){await report({error:'多个同名结果，未任意选择'});return;}
 if(Date.now()-started>20000){await report({error:'搜索结果没有唯一精确名称；保留候选'});return;}
}
if(Date.now()-started>5000&&lookupStage===0&&!/Search Results/.test(text)){lookup();return;}
if(Date.now()-started>110000)await report({error:'页面未返回可识别免费摘要'});
}catch{ /* A navigation or service restart may temporarily disconnect the port. */ }}
chrome.runtime.onMessage.addListener(message=>{if(message.action==='resume'){finished=false;started=Date.now();step();}});
setInterval(step,1800);step();
