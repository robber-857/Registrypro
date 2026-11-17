"""Local queue and evidence validation for the narrowly scoped ASIC extension."""
import json
import re
import secrets
import threading
from datetime import datetime,timezone,date
from urllib.parse import urlencode,urlsplit
import registration
from auto_registration import parse_abr,normalized,valid_abn

LOCK=threading.RLock()
STATE={'state':'idle','tasks':[],'message':'插件尚未启动任务','results':[]}


def aliases(name,address=''):
    name=re.sub(r'[®™]','',name).strip()
    values={name,re.split(r'\s+[@–]|\s+-\s+',name)[0]}
    for value in list(values):
        values.add(re.sub(r'\s+pty\.?\s+(?:ltd\.?|limited)\.?$','',value,flags=re.I))
    # Remove only an explicit suburb suffix found in the requested location.
    for suburb in ('Strathfield','Burwood','Homebush','Lidcombe','Greenacre','Strathfield South'):
        if suburb.casefold() in address.casefold():
            for value in list(values):values.add(re.sub(r'\s+'+suburb+r'$','',value,flags=re.I))
    return {normalized(v) for v in values if len(normalized(v))>=4}


def compatible(name,row):
    return bool(aliases(name)&aliases(row['name'],row.get('address','')))


def address_key(value):
    value=value.casefold().replace('new south wales','nsw')
    for long,short in [('street','st'),('road','rd'),('boulevard','boulevarde'),('parade','pde'),('avenue','ave'),('suite','unit'),('shop','unit')]:
        value=re.sub(r'\b'+long+r'\b',short,value)
    return normalized(value)


def same_address(a,b):
    # Full address comparison; a shared suburb/postcode is never sufficient.
    if not re.search(r'\d',a) or not re.search(r'\b\d{4}\b',a):return False
    if address_key(a)==address_key(b):return True
    def parts(value):
        value=value.casefold().replace('new south wales','nsw')
        for long,short in [('street','st'),('road','rd'),('parade','pde'),('avenue','ave')]:value=re.sub(r'\b'+long+r'\b',short,value)
        street=re.search(r'(?<!\d)(\d+[a-z]?(?:-\d+[a-z]?)?)\s+([a-z ]+?)\s+(st|rd|pde|ave|boulevarde)\b',value)
        postcode=re.search(r'\b(nsw|vic|qld|sa|wa|tas|nt|act)\s+(\d{4})\b',value)
        unit=re.search(r'\b(?:suite|unit|shop)\s+([a-z0-9]+)',value)
        if not unit:unit=re.search(r'\b([a-z0-9]+)\s*/\s*\d+',value)
        if not street or not postcode:return None
        return (street.group(1),normalized(street.group(2)),street.group(3),postcode.groups(),unit.group(1) if unit else '')
    first,second=parts(a),parts(b)
    return bool(first and second and first==second)


def build(rows,generation=None):
    from local_leads import fetch
    tasks=[]
    for row in rows:
        with LOCK:
            if generation is not None and (STATE.get('generation')!=generation or STATE['state']=='stopped'):return
            STATE['message']='准备浏览器查询：'+row['name']
        known=registration.apply([row])[0]
        candidates=json.loads(known.get('auto_candidates') or '[]')
        if known.get('abn'):candidates.insert(0,{'abn':known['abn']})
        added=set()
        def add(kind,url,name,abn='',acn=''):
            token=(kind,url)
            if token in added:return
            added.add(token)
            tasks.append({'id':secrets.token_hex(12),'row':row,'key':registration.key(row),'kind':kind,
                          'url':url,'name':name,'abn':abn,'acn':acn,'state':'queued'})
        for candidate in candidates[:6]:
            abn=candidate.get('abn','')
            if not valid_abn(abn):continue
            try:
                html,_,_=fetch('https://abr.business.gov.au/ABN/View?id='+abn,ttl=3600)
                info=parse_abr(html,abn)
                for name in info['names']:
                    if (compatible(name,row) or (len(info['names'])==1 and compatible(info['legal_name'],row))) and info['links'].get(name):add('business',info['links'][name],name,abn)
                if (compatible(info['legal_name'],row) or known.get('abn')==abn) and info['company_link']:
                    add('company',info['company_link'],info['legal_name'],abn,info['acn'])
            except Exception:continue
        query=re.sub(r'[®™]','',row['name']).strip()
        # A real name search also covers cases where an unrelated website provider ABN was found.
        add('business','https://connectonline.asic.gov.au/RegistrySearch/faces/landing/panelSearch.jspx?'+urlencode({'searchTab':'bnsearch','searchName':query,'searchNumber':'','searchType':'Bn'}),query)
    with LOCK:
        if generation is not None and (STATE.get('generation')!=generation or STATE['state']=='stopped'):return
        STATE.update(state='ready',tasks=tasks,message=f'已准备 {len(tasks)} 项浏览器查询',results=[])


def start(rows):
    with LOCK:
        if STATE['state'] in ('preparing','ready','running'):raise ValueError('已有浏览器任务，请先停止或完成')
        generation=secrets.token_hex(12)
        STATE.update(state='preparing',tasks=[],results=[],message='准备官方查询链接',generation=generation)
    def run():
        try:build(rows,generation)
        except Exception as exc:
            with LOCK:
                if STATE.get('generation')==generation and STATE['state']!='stopped':STATE.update(state='error',message=str(exc))
    threading.Thread(target=run,daemon=True).start()


def snapshot():
    with LOCK:
        return {'state':STATE['state'],'message':STATE['message'],'total':len(STATE['tasks']),
                'completed':sum(t['state']=='done' for t in STATE['tasks']),'results':list(STATE['results'])}


def claim():
    with LOCK:
        if STATE['state'] in ('idle','preparing','stopped','done','error'):return None
        for task in STATE['tasks']:
            if task['state'] in ('queued','running'):
                task['state']='running';STATE.update(state='running',message='浏览器正在核对：'+task['row']['name'])
                return {k:v for k,v in task.items() if k!='row'}
        STATE.update(state='done',message='浏览器任务已完成，结果已写入名单和 CSV')
        return None


def accept(task,payload):
    from local_leads import fetch
    row=task['row'];current=registration.apply([row])[0]
    if current['identity_status'] in ('人工核实','存在冲突'):return '保留人工核验／冲突记录'
    url=payload.get('url','')
    if urlsplit(url).scheme!='https' or urlsplit(url).hostname!='connectonline.asic.gov.au':raise ValueError('结果来源必须是 ASIC 官方页面')
    text=payload.get('text','')
    if not isinstance(text,str) or len(text)>150000:raise ValueError('摘要大小不正确')
    # Parse the observed summary again on the server rather than trusting supplied fields.
    kind='business' if 'Business Name Summary' in text else 'company' if 'Company Summary' in text else ''
    if kind!=task['kind']:raise ValueError('摘要类型与任务不符')
    def field(label):
        m=re.search(r'(?:^|\n)\s*'+re.escape(label)+r'\s*:\s*([^\n]+)',text,re.I)
        return m.group(1).strip() if m else ''
    name=field('Business name' if kind=='business' else 'Name')
    if normalized(name)!=normalized(task['name']):raise ValueError('摘要名称与任务不符')
    numbers=re.findall(r'ABN\s*:\s*([\d ]{11,15})',text,re.I)
    abns={re.sub(r'\D','',n) for n in numbers if valid_abn(re.sub(r'\D','',n))}
    if len(abns)!=1:raise ValueError('摘要没有唯一有效 ABN')
    abn=next(iter(abns))
    if task['abn'] and abn!=task['abn']:raise ValueError('摘要 ABN 与任务不符')
    if current['identity_status']=='自动匹配' and current.get('abn') and current['abn']!=abn:
        return '不同 ABN 与已确认主体冲突，保留原资料及本次证据'
    if field('Status').casefold()!='registered':return '官方状态不是 Registered，未确认门店主体'
    html,_,_=fetch('https://abr.business.gov.au/ABN/View?id='+abn,ttl=3600)
    info=parse_abr(html,abn)
    if not info['registration_status'].startswith('Active'):return 'ABN 不是 Active，未确认门店主体'
    previously_matched=current.get('abn')==abn and current['identity_status']=='自动匹配'
    matched=previously_matched
    if kind=='business':
        if normalized(name) not in {normalized(n) for n in info['names']}:raise ValueError('ABN 当前 Business Names 未列该名称')
        address=field('Principal place of business')
        matched=matched or ((compatible(name,row) or (len(info['names'])==1 and compatible(info['legal_name'],row))) and same_address(address,row['address']))
        raw=field('Renewal date') or field('Next renewal date');date_field='business_name_renewal_date';source_field='renewal_source'
    else:
        acn=re.sub(r'\D','',field('ACN'))
        if acn!=info['acn'] or (task['acn'] and acn!=task['acn']):raise ValueError('ACN 不一致')
        address=field('Locality of registered office')
        # Company locality alone never establishes a shop relationship.
        raw=field('Next review date');date_field='company_next_review_date';source_field='review_source'
    if not matched:return '已读取官方摘要；门店关联仍不足，已保存证据但未确认主体'
    if not raw:raise ValueError('摘要没有对应日期')
    try:value=datetime.strptime(raw,'%d/%m/%Y').date().isoformat()
    except ValueError:raise ValueError('官方日期格式无法识别')
    data={k:current.get(k,'') for k,_ in registration.FIELDS}
    data.update(abn=abn,legal_name=info['legal_name'],acn=info['acn'],identity_status='自动匹配',
                identity_source='https://abr.business.gov.au/ABN/View?id='+abn,registration_status=info['registration_status'],
                identity_checked_at=datetime.now(timezone.utc).isoformat(),auto_reason='ASIC 浏览器摘要与 ABN 当前记录交叉对应',
                date_status='已读取浏览器中的 ASIC 日期；其他空白日期尚未取得')
    if kind=='business':
        data['business_name']=name
        if not previously_matched:
            data['identity_evidence']='ASIC 登记商业名称与门店名称对应，Principal place of business 全地址一致：'+address+'；ABN 当前持有人交叉核对。'+url
    data[date_field]=value;data[source_field]=url
    registration.save_browser(task['key'],data)
    return '已自动确认并保存日期 '+value


def result(payload):
    with LOCK:
        task=next((t for t in STATE['tasks'] if t['id']==payload.get('id')),None)
        if not task or task['state']!='running':raise ValueError('任务已过期或尚未领取')
    evidence={'task':task,'result':payload,'received_at':datetime.now(timezone.utc).isoformat()}
    if payload.get('error'):message='浏览器未完成：'+str(payload['error'])[:300]
    else:
        try:message=accept(task,payload)
        except ValueError as exc:message='未采纳：'+str(exc)
    folder=registration.PATH.parent/'asic_browser_evidence';folder.mkdir(parents=True,exist_ok=True)
    evidence['outcome']=message
    (folder/(task['id']+'.json')).write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    with LOCK:
        task['state']='done';STATE['results'].append({'name':task['row']['name'],'message':message})
    return message


def stop():
    with LOCK:STATE.update(state='stopped',message='浏览器队列已停止；已保存的结果保留')
