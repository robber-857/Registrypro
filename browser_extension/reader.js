// Reads only the assigned public ASIC tab. No payment, login or CAPTCHA interaction.
let task=null,finished=false,clicked=false,started=Date.now(),lookupStage=0,formWaits=0,clickAt=0,clickAttempts=0,pageNumber=1,pageWait=0,pageSignature="",seenResults=[],returnPage=0,selectedName="",busy=false;
const norm=s=>String(s).toLowerCase().replace(/[^a-z0-9]/g,'');
const visible=e=>!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);
function challengeVisible(e){
 if(!visible(e))return false;
 const r=e.getBoundingClientRect();
 if(r.width<20||r.height<20||r.right<=0||r.bottom<=0||r.left>=innerWidth||r.top>=innerHeight)return false;
 for(let p=e;p;p=p.parentElement){const style=getComputedStyle(p);if(style.display==='none'||style.visibility==='hidden'||Number(style.opacity)===0)return false;}
 return true;
}
let identityContent='',identitySince=Date.now();
function ocrIdentityMatches(text){
 const label=task.kind==='business'?'Business name':'Name';
 const match=text.match(new RegExp('(?:^|\\n)\\s*'+label+'\\s*:\\s*([^\\n]+)','i'));
 if(match)return norm(match[1])===norm(selectedName||task.name)||(task.allowed_names||[]).includes(norm(match[1]));
 const query=text.match(/results? found for\s+["“']([^"”']+)["”']/i);
 return !!query&&[task.name,task.abn,task.acn].filter(Boolean).some(value=>norm(query[1])===norm(value));
}
let seenCancelled=[],ocrObservation=null,ocrKey='',ocrAttempts=0,ocrFailure='';
async function inspectScreenshot(text){ocrKey=text;ocrAttempts++;const response=await chrome.runtime.sendMessage({action:'ocr-inspect',id:task.id});ocrObservation=response.ocr||null;ocrFailure=response.error||'';if(ocrFailure)await chrome.runtime.sendMessage({action:'diagnostic',id:task.id,reason:'截图识别失败：'+ocrFailure});}
let recovering=false,lastContent='',changedAt=Date.now(),challengeSince=null,clearSince=null,technicalSince=null;
async function recoverPage(reason){
 if(recovering)return;recovering=true;
 const response=await chrome.runtime.sendMessage({action:'recover',id:task.id,reason});
 if(response.error){recovering=false;await report({error:reason+'；'+response.error});}
}
async function report(result){if(finished)return;finished=true;const response=await chrome.runtime.sendMessage({action:'result',id:task.id,metrics:{result_pages:pageNumber,detail_clicks:clickAttempts,ocr_attempts:ocrAttempts,ocr_engine:ocrObservation?.engine||'',ocr_error:ocrFailure},...result});if(response.error){finished=false;}}
function lookup(){
 const select=document.querySelector('select[id$="templateSearchTypesListOfValuesId::content"]');
 if(!select)return false;
 if(lookupStage===0){const option=Array.from(select.options).find(o=>o.textContent.trim()===(task.kind==='business'?'Business names index':'Organisation and Business Names'));if(!option)return false;select.value=option.value;select.dispatchEvent(new Event('change',{bubbles:true}));lookupStage=1;return true;}
 const suffix=task.kind==='business'?(task.abn?'searchNumber::content':'searchName::content'):'templateSearchInputText::content';
 const input=document.querySelector('input[id$="'+suffix+'"]');if(!input||!visible(input)){if(++formWaits===4)document.querySelector('[id="bnConnectionTemplate:pt_s5:searchButtonId"]')?.click();return true;}
 if(lookupStage===1){
  if(task.kind==='business'){const other=document.querySelector('input[id$="'+(task.abn?'searchName::content':'searchNumber::content')+'"]');if(other){other.value='';other.dispatchEvent(new Event('change',{bubbles:true}));}}
  input.value=task.kind==='business'&&task.abn?task.abn:task.kind==='company'&&task.acn?task.acn:task.name;input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.blur();lookupStage=2;return true;}
 if(lookupStage===2){document.querySelector('[id="bnConnectionTemplate:pt_s5:searchButtonId"]')?.click();lookupStage=3;started=Date.now();return true;}
 return false;
}
async function step(){if(busy||recovering)return;busy=true;try{const response=await chrome.runtime.sendMessage({action:'task',id:task?.id});if(!response.task)return;task=response.task;if(finished)return;
const main=document.querySelector('main,[role="main"]');let text=(main||document.body)?.innerText||'';const observedText=text;
if(/verify you are human|captcha|unusual traffic|access denied/i.test(text)||Array.from(document.querySelectorAll('iframe[title]')).some(e=>/recaptcha.*challenge|challenge.*recaptcha/i.test(e.title)&&challengeVisible(e))){
 clearSince=null;
 if(challengeSince===null)challengeSince=Date.now();
 if(!response.monitorOnly&&Date.now()-challengeSince>=2500){await chrome.runtime.sendMessage({action:'blocked',id:task.id});}
 return;
}
challengeSince=null;
const marker=task.kind==='business'?'Business Name Summary':'Company Summary';
if(response.monitorOnly){
 const normal=/Search ASIC Registers/.test(document.body?.innerText||text)&&(/Search Results|Business Name Summary|Company Summary/.test(text)||document.querySelector('select[id$="templateSearchTypesListOfValuesId::content"]'));
 if(!normal){clearSince=null;return;}
 if(clearSince===null)clearSince=Date.now();
 if(Date.now()-clearSince>=5000){await chrome.runtime.sendMessage({action:'verification-cleared',id:task.id});clearSince=null;started=Date.now();changedAt=Date.now();}
 return;
}
clearSince=null;
// Server errors are not empty search results. Allow a cooldown before bounded recovery.
if(/currently experiencing technical difficulties|temporarily unavailable|service unavailable|internal server error/i.test(text)){
 if(technicalSince===null)technicalSince=Date.now();
 if(Date.now()-technicalSince>=15000)await recoverPage('ASIC 技术故障，待重试');
 return;
}
technicalSince=null;
if(observedText!==identityContent){identityContent=observedText;identitySince=Date.now();}
if(task.screenshot_first&&ocrIdentityMatches(text)&&(/Search Results/.test(text)||text.includes(marker))&&ocrKey!==observedText&&ocrAttempts<3){if(Date.now()-identitySince<2500)return;await inspectScreenshot(observedText);return;}
if(ocrKey===observedText&&ocrObservation?.page_type==='business_summary'&&ocrObservation.text)text=ocrObservation.text;
if(text!==lastContent){lastContent=text;changedAt=Date.now();}
if(!text.trim()&&Date.now()-started>12000){await recoverPage('ASIC 空白页');return;}
if(Date.now()-changedAt>45000&&!text.includes(marker)){await recoverPage('页面内容 45 秒未推进');return;}
if(Date.now()-started>110000){const ocr=await chrome.runtime.sendMessage({action:'ocr',id:task.id});if(ocr.error)await report({error:'页面未返回可识别免费摘要；'+ocr.error});else finished=true;return;}
if(lookupStage===1||lookupStage===2){lookup();return;}
if(text.includes(marker)){
 const label=task.kind==='business'?'Business name':'Name';const match=text.match(new RegExp('(?:^|\\n)\\s*'+label+'\\s*:\\s*([^\\n]+)','i'));
 if(match&&(norm(match[1])===norm(selectedName||task.name)||(task.allowed_names||[]).includes(norm(match[1])))){if(/(?:^|\n)\s*Status\s*:\s*(Cancelled|Deregistered)\b/i.test(text)){await report({error:'指定商号已注销（Cancelled），已记录并跳过',text:text.slice(0,150000)});return;}await report({text:text.slice(0,150000)});return;}
 if(Date.now()-started>4000&&lookupStage===0){lookup();return;}
 if(Date.now()-started>20000){if(!match){const ocr=await chrome.runtime.sendMessage({action:'ocr',id:task.id});if(ocr.error)await report({error:'文字读取失败；'+ocr.error});else finished=true;}else await report({error:'摘要名称与指定任务不一致'});return;}
}
// ASIC can retain the previous query body after a URL navigation. Submit the
// assigned search through its form before accepting any search-list outcome.
const queryLabel=text.match(/results? found for\s+["“']([^"”']+)["”']/i);
const queryMatches=!!queryLabel&&[task.name,task.abn,task.acn].filter(Boolean).some(value=>norm(queryLabel[1])===norm(value));
if(!text.includes(marker)&&lookupStage===0&&!queryMatches&&lookup())return;
if(/0 results found|no results found/i.test(text)){if(!queryMatches)return;await report({error:'官方名称搜索没有结果'});return;}
if(/Search Results/.test(text)&&(!clicked||Date.now()-clickAt>8000)){
 if(clickAttempts>=3){await report({error:'精确结果链接三次未响应'});return;}
 const scope=main||document;
 const links=Array.from(scope.querySelectorAll('a')).filter(e=>visible(e)&&e.id?.endsWith(':orgName'));
 // Isolated fixtures and legacy company tables may expose only the exact link.
 const sourceLinks=links.length?links:Array.from(scope.querySelectorAll('a')).filter(e=>visible(e)&&norm(e.innerText)===norm(task.name));
 const clean=s=>String(s).replace(/: Select result.*$/i,'').trim();
 const tokens=s=>clean(s).toLowerCase().replace(/&/g,' and ').replace(/\b(pty|ltd|limited)\b/g,'').replace(/[^a-z0-9 ]/g,' ').split(/\s+/).filter(Boolean);
 function score(name){if(norm(name)===norm(task.name)||(task.allowed_names||[]).includes(norm(name)))return 1;const a=tokens(name),b=tokens(task.name);if(a.join('')===b.join(''))return 1;if([...a,...b].some(v=>['plaza','centre','center','mall'].includes(v))&&a.sort().join()!=b.sort().join())return 0;return a.filter(v=>b.includes(v)).length/Math.max(a.length,b.length);}
 const observed=sourceLinks.map(link=>{const tr=link.closest?.('tr');let row=tr?.innerText||'';const visual=ocrKey===observedText?ocrObservation?.rows?.find(r=>norm(r.name)===norm(clean(link.innerText))):null;if(visual)row+=' '+visual.status;const cells=tr?Array.from(tr.querySelectorAll('td')).map(c=>c.innerText):[];const abn=(visual?.abn||cells[2]||'').replace(/\D/g,'');return {link,name:clean(link.innerText),row,abn,score:score(clean(link.innerText)),page:pageNumber};});
 for(const r of observed.filter(r=>/cancelled|deregistered/i.test(r.row)&&r.score>=0.82)){if(!seenCancelled.some(x=>x.name===r.name&&x.abn===r.abn))seenCancelled.push({name:r.name,abn:r.abn,status:'Cancelled'});}
 const candidates=observed.filter(r=>!(/cancelled|deregistered|former name/i.test(r.row))&&!r.name.includes('*')&&r.score>=0.65&&(!task.abn||!r.abn||r.abn===task.abn));
 const signature=sourceLinks.map(l=>l.innerText+' '+(l.closest?.('tr')?.innerText||'')).join('|');
 if(pageWait&&signature===pageSignature&&Date.now()-pageWait<10000)return;
 pageWait=0;
 const preferred=candidates.filter(r=>/\bRegistered\b/i.test(r.row));
 const eligible=preferred.length?preferred:candidates;
 if(returnPage){const chosen=eligible.find(r=>r.name===selectedName);if(chosen){clicked=true;clickAt=Date.now();clickAttempts++;chosen.link.click();return;}}
 if(task.abn){const exact=eligible.filter(r=>r.abn===task.abn);if(exact.length===1){selectedName=exact[0].name;clicked=true;clickAt=Date.now();clickAttempts++;exact[0].link.click();return;}}
 for(const candidate of eligible){if(!seenResults.some(r=>r.name===candidate.name&&r.abn===candidate.abn&&r.row===candidate.row))seenResults.push({...candidate,link:null});}
 const next=document.querySelector('[id$=":pagingNextButton"]');
 if(next&&!next.disabled&&next.getAttribute('aria-disabled')!=='true'&&pageNumber<10){pageSignature=signature;pageWait=Date.now();pageNumber++;next.click();return;}
 const sorted=seenResults.sort((a,b)=>b.score-a.score);
 const unique=sorted.length===1||(sorted.length>1&&sorted[0].score-sorted[1].score>=0.2);
 if(unique){const best=sorted[0];selectedName=best.name;if(best.page!==pageNumber){const back=document.querySelector('[id$=":pageButton'+best.page+'"]');if(back){returnPage=best.page;pageNumber=best.page;pageSignature=signature;pageWait=Date.now();back.click();return;}}
 const chosen=eligible.find(r=>r.name===best.name&&r.abn===best.abn);if(chosen){clicked=true;clickAt=Date.now();clickAttempts++;chosen.link.click();return;}}
 if(sorted.length>1){await report({error:'多个当前主体仍有歧义；已按状态及预期 ABN 筛选',candidates:sorted});return;}
 if(!sorted.length&&seenCancelled.length){await report({error:'仅找到已注销（Cancelled）商号，已记录并跳过',candidates:seenCancelled});return;}
 if(Date.now()-started>12000&&ocrAttempts<1){await inspectScreenshot(observedText);return;}
 if(Date.now()-started>12000){await report({error:pageNumber>=10?'搜索达到 10 页上限，未找到唯一有效记录':'未找到对应的非 Cancelled 当前名称'});return;}

}
if(Date.now()-started>5000&&lookupStage===0&&!/Search Results/.test(text)){lookup();return;}
if(Date.now()-started>110000)await report({error:'页面未返回可识别免费摘要'});
}catch(error){try{await chrome.runtime.sendMessage({action:'diagnostic',id:task?.id,reason:'读取阶段 '+lookupStage+'：'+String(error.message||error).slice(0,180)});}catch{} }finally{busy=false;}}
chrome.runtime.onMessage.addListener(message=>{if(message.action==='resume'){finished=false;started=Date.now();step();}});
setInterval(step,900);step();
