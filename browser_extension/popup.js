const $=id=>document.getElementById(id);
chrome.storage.session.get('config').then(({config})=>{if(config){$('token').value=config.token;$('scope').value=config.scope;$('delay').value=String(config.delay);}});
for(const action of ['start','pause','resume'])$(action).onclick=async()=>{try{const response=await chrome.runtime.sendMessage({action,config:{token:$('token').value.trim(),scope:$('scope').value,delay:Number($('delay').value)}});if(response.error)throw Error(response.error);$('status').textContent=response.message;}catch(error){$('status').textContent=error.message;}};
async function status(){const result=await chrome.runtime.sendMessage({action:'status'});$('status').textContent=result.message||'准备就绪';}
status();setInterval(status,1500);
