const $=id=>document.getElementById(id);
chrome.storage.session.get('config').then(({config})=>{if(config){$('token').value=config.token;$('scope').value=config.scope;$('delay').value=String(config.delay);for(const [id,key] of [['follow','follow'],['refresh','refresh'],['company','include_company']])$(id).checked=!!config[key];}});
for(const action of ['start','pause','resume'])$(action).onclick=async()=>{try{const response=await chrome.runtime.sendMessage({action,config:{token:$('token').value.trim(),scope:$('scope').value,delay:Number($('delay').value),refresh:$('refresh').checked,include_company:$('company').checked,follow:$('follow').checked}});if(response.error)throw Error(response.error);$('status').textContent=response.message;}catch(error){$('status').textContent=error.message;}};
async function status(){const result=await chrome.runtime.sendMessage({action:'status'});$('status').textContent=result.message||'准备就绪';}
status();setInterval(status,1500);

$('reload').onclick=()=>chrome.runtime.reload();

$('enableocr').onclick=async()=>{const granted=await chrome.permissions.request({permissions:['activeTab']});if(granted){const r=await chrome.runtime.sendMessage({action:'use-ocr-tab'});$('status').textContent=r.error||'当前 ASIC 页已启用截图；查询队列将复用此标签页';}else $('status').textContent='未授权截图；仍使用页面文字读取';};
