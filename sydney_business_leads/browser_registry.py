"""Local queue and evidence validation for the narrowly scoped ASIC extension."""
import json
import re
import secrets
import time
from name_matching import similarity, rank_names, words
import threading
import html as html_module
from datetime import datetime,timezone,date
from urllib.parse import urlencode,urlsplit
import registration
from auto_registration import parse_abr,normalized,valid_abn

LOCK=threading.RLock()
STATE={'state':'idle','tasks':[],'message':'插件尚未启动任务','results':[]}


def checkpoint():
    with LOCK:
        path=registration.PATH.parent/'browser_queue.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(STATE,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(path)


def export_progress():
    from local_leads import csv_bytes
    with LOCK:
        rows=STATE.get('rows',[])
        if not rows:return
        folder=registration.PATH.parent/'asic_runs';folder.mkdir(parents=True,exist_ok=True)
        name=STATE.get('generation') or 'latest'
        (folder/(name+'.csv')).write_bytes(csv_bytes(rows))
        (folder/(name+'.json')).write_text(json.dumps(snapshot(),ensure_ascii=False,indent=2),encoding='utf-8')


_saved_queue=registration.PATH.parent/'browser_queue.json'
if _saved_queue.exists():
    try:
        STATE.update(json.loads(_saved_queue.read_text(encoding='utf-8')))
        if STATE['state'] not in ('done','stopped','idle'):
            STATE.update(state='paused',message='已恢复浏览器队列；重新配对后继续')
    except (OSError,ValueError):pass


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
    return bool(aliases(name)&aliases(row['name'],row.get('address',''))) or similarity(name,row['name'],row.get('address','')) >= 0.82


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
        unit=re.search(r'\b(?:suite|unit|shop|level)\s+([a-z0-9]+)',value)
        if not unit:unit=re.search(r'\b([a-z0-9]+)\s*/\s*\d+',value)
        if not street or not postcode:return None
        prefix=value[:street.start()].strip(' ,/')
        if not unit and prefix:
            # ASIC sometimes omits the level/unit label; never discard that number.
            if re.fullmatch(r'[0-9]+[a-z]?',prefix):
                unit=re.match(r'([a-z0-9]+)',prefix)
            else:return None
        return (street.group(1),normalized(street.group(2)),street.group(3),postcode.groups(),unit.group(1) if unit else '')
    first,second=parts(a),parts(b)
    return bool(first and second and first==second)


def website_address(row,official_address):
    """Corroborate missing unit details using the shop's own structured contact data."""
    from local_leads import fetch
    phone=re.sub(r'\D','',row.get('phone',''))[-9:]
    if len(phone)!=9:return ''
    homepage=row.get('website','')
    url=row.get('contact_page') or homepage
    if not url or urlsplit(url).hostname!=urlsplit(homepage).hostname:return ''
    try:
        source,final_url,_=fetch(url)
        if (urlsplit(final_url).hostname or '').removeprefix('www.')!=(urlsplit(homepage).hostname or '').removeprefix('www.'):return ''
        def walk(value):
            if isinstance(value,dict):
                address=value.get('address')
                if isinstance(address,dict) and phone==re.sub(r'\D','',str(value.get('telephone','')))[-9:]:
                    full=' '.join(str(address.get(k,'')) for k in ('streetAddress','addressLocality','addressRegion','postalCode'))
                    if same_address(official_address,full):return full
                for child in value.values():
                    found=walk(child)
                    if found:return found
            elif isinstance(value,list):
                for child in value:
                    found=walk(child)
                    if found:return found
            return ''
        for raw in re.findall(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>',source,re.I|re.S):
            try:found=walk(json.loads(html_module.unescape(raw)))
            except (ValueError,TypeError):continue
            if found:return '官网同一电话对应地址：'+found+'；'+url
    except Exception:pass
    return ''


def build(rows,generation=None,refresh=False,include_company=False):
    from local_leads import fetch
    tasks=[]; subjects={}; details={}
    cache_path=registration.PATH.parent/'asic_cache.json'
    try:cache=json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
    except (ValueError,OSError):cache={}
    for row in rows:
        with LOCK:
            if generation is not None and (STATE.get('generation')!=generation or STATE['state']=='stopped'):return
            STATE['message']='准备浏览器查询：'+row['name']
        known=registration.apply([row])[0]
        candidates=json.loads(known.get('auto_candidates') or '[]')
        if known.get('abn'):candidates.insert(0,{'abn':known['abn']})
        ranked=[]
        for candidate in candidates[:8]:
            abn=candidate.get('abn','')
            if not valid_abn(abn):continue
            try:
                if abn not in details:
                    html,_,_=fetch('https://abr.business.gov.au/ABN/View?id='+abn,ttl=86400)
                    details[abn]=parse_abr(html,abn)
                info=details[abn]
                if not info['registration_status'].startswith('Active'):continue
                names=rank_names(info,row)
                if names:
                    score,name=names[0]
                    confirmed=known.get('abn')==abn and known.get('identity_status') in ('自动匹配','人工核实')
                    if confirmed and known.get('business_name') in info['names']:name=known['business_name'];score=1
                    if score>=0.65 or confirmed:ranked.append((score+int(confirmed),name,abn,info))
            except Exception:continue
        ranked.sort(key=lambda v:(-v[0],v[1],v[2]))
        query=' '.join(words(row['name'],row.get('address',''))) or re.sub(r'[®™]','',row['name']).strip()
        name,abn,info=query,'',{}
        if ranked:
            _,name,abn,info=ranked[0]
        # A ranked ABN is a search constraint, never a confirmed identity.
        subject=(abn+'|'+normalized(name)) if abn else registration.key(row)
        if subject in subjects:
            subjects[subject]['related_rows'].append(row)
            continue
        url=info.get('links',{}).get(name) or 'https://connectonline.asic.gov.au/RegistrySearch/faces/landing/panelSearch.jspx?'+urlencode({'searchTab':'bnsearch','searchName':name,'searchType':'Bn'})
        task={'id':secrets.token_hex(12),'row':row,'related_rows':[],'key':registration.key(row),'kind':'business',
              'url':url,'name':name,'abn':abn,'acn':'','state':'queued','search_only':not bool(abn),
              'alternatives':[n for score,n in rank_names(info,row) if n!=name and score>=0.65][:2] if info else [],'candidate_abns':list(dict.fromkeys(v[2] for v in ranked)),
              'name_ambiguous':len(ranked)>1 and ranked[0][2]!=ranked[1][2] and ranked[0][0]-ranked[1][0]<0.2,
              'screenshot_first':__import__('ocr_vision').configuration().get('enabled',False),
              'allowed_names':list(aliases(row['name'],row.get('address',''))|{normalized(name)}),'created_at':time.time()}
        subjects[subject]=task;tasks.append(task)
        cached=cache.get(abn+'|'+normalized(name),{})
        if not refresh and cached and 0<=time.time()-cached.get('fetched_at',0)<86400:
            try:
                outcome=accept(task,cached['payload'],datetime.fromtimestamp(cached['fetched_at'],timezone.utc).isoformat())
                task.update(state='done',cached=True,cache_outcome=outcome,cache_fetched_at=cached['fetched_at'])
            except Exception:pass
        elif not refresh and known.get('business_name')==name and known.get('business_name_renewal_date') and known.get('renewal_checked_at'):
            try:
                age=(datetime.now(timezone.utc)-datetime.fromisoformat(known['renewal_checked_at'])).total_seconds()
                if 0<=age<86400:task.update(state='done',cached=True)
            except (KeyError,ValueError):pass
        if include_company and info.get('company_link'):
            tasks.append({**task,'id':secrets.token_hex(12),'kind':'company','name':info['legal_name'],'acn':info['acn'],'url':info['company_link'],'state':'queued','cached':False})
    with LOCK:
        if generation is not None and (STATE.get('generation')!=generation or STATE['state']=='stopped'):return
        STATE.update(rows=rows)
        STATE.update(state='ready',tasks=tasks,message=f'已准备 {len(tasks)} 项查询（默认每主体一个名称）',results=[{'name':t['row']['name'],'message':'24 小时摘要缓存：'+t.get('cache_outcome','已确认日期'), 'fetched_at':t.get('cache_fetched_at'),'cached':True} for t in tasks if t.get('cached')])
        checkpoint()


def start(rows,refresh=False,include_company=False):
    with LOCK:
        if STATE['state'] in ('preparing','ready','running','paused'):raise ValueError('已有浏览器任务，请先停止或完成')
        STATE.pop('finished_at',None)
        generation=secrets.token_hex(12)
        STATE.update(state='preparing',tasks=[],results=[],message='准备官方查询链接',generation=generation,started_at=time.time(),rows=rows)
        checkpoint()
    def run():
        try:build(rows,generation,refresh,include_company)
        except Exception as exc:
            with LOCK:
                if STATE.get('generation')==generation and STATE['state']!='stopped':STATE.update(state='error',message=str(exc))
    threading.Thread(target=run,daemon=True).start()


def snapshot():
    with LOCK:
        return {'state':STATE['state'],'message':STATE['message'],'pause_reason':STATE.get('pause_reason'),'total':len(STATE['tasks']),
                'completed':sum(t['state']=='done' for t in STATE['tasks']),'results':list(STATE['results']),'elapsed_seconds':round(STATE.get('finished_at',time.time())-STATE.get('started_at',time.time()),1),'generation':STATE.get('generation'),'cache_hits':sum(bool(t.get('cached')) for t in STATE['tasks'])}


def claim():
    with LOCK:
        if STATE['state'] in ('idle','preparing','stopped','done','error','paused'):return None
        for task in STATE['tasks']:
            if task['state'] in ('queued','running'):
                task.setdefault('started_at',time.time());task['state']='running';STATE.update(state='running',message='浏览器正在核对：'+task['row']['name'])
                checkpoint()
                return {k:v for k,v in task.items() if k not in ('row','related_rows')}
        STATE.update(state='done',finished_at=time.time(),message='浏览器任务已完成，结果已写入名单和 CSV')
        checkpoint();export_progress()
        return None


def accept_business_name(task, current, field, abns, url, checked_at):
    """Confirm the requested name/date pair, without claiming shop-owner identity."""
    from local_leads import fetch
    name=field('Business name');raw=field('Renewal date') or field('Next renewal date')
    if field('Status').casefold() in ('cancelled','deregistered'):return '指定商号已注销（Cancelled），已记录并跳过'
    if field('Status').casefold()!='registered':return '官方状态不是 Registered，未确认商号日期'
    if not raw:raise ValueError('摘要没有 Renewal date')
    try:value=datetime.strptime(raw,'%d/%m/%Y').date().isoformat()
    except ValueError:raise ValueError('官方日期格式无法识别')
    abn=next(iter(abns)) if len(abns)==1 else ''
    info={};note=''
    if abn:
        try:
            html,_,_=fetch('https://abr.business.gov.au/ABN/View?id='+abn,ttl=3600)
            info=parse_abr(html,abn)
        except Exception:note='；ABN 补充资料暂未取得'
    name_matches=compatible(name,task['row']) or (bool(info) and any(score>=0.82 and n==name for score,n in rank_names(info,task['row'])))
    ambiguous=task.get('name_ambiguous',False)
    if not name_matches or ambiguous:
        registration.update_browser(task['key'],dict(candidate_business_name=name,candidate_abn=abn,candidate_renewal_date=value,candidate_source=url,match_status='名称歧义候选' if ambiguous else '名称待核验候选',asic_status=field('Status')))
        return '已读取官方摘要；名称仍有歧义或匹配不足，保留候选'
    if current.get('identity_status')=='自动匹配' and current.get('abn') and abn and current['abn']!=abn:
        return '不同 ABN 与已确认主体冲突，保留原资料及本次证据'
    address=field('Principal place of business')
    address_note=('地址一致' if same_address(address,task['row'].get('address','')) else '地址不同或不完整，仅记录，不阻止商号日期确认')
    data=dict(business_name=name,business_name_renewal_date=value,renewal_source=url,
        renewal_checked_at=checked_at or datetime.now(timezone.utc).isoformat(),
        match_status='商号及续期日已确认',asic_status=field('Status'),
        date_status='已确认 Business Name 与 Renewal date；不代表门店持有人已核实',
        auto_reason='按商号名称与有效续期日匹配；'+address_note+'；ASIC 地址：'+(address or '未提供')+'；目录地址：'+task['row'].get('address','')+note,
        candidate_business_name='',candidate_abn='',candidate_renewal_date='',candidate_source='')
    if abn:data['abn']=abn
    for key in ('legal_name','acn','registration_status'):
        if info.get(key):data[key]=info[key]
    # Keep independent, previously established owner identity; never promote it from name alone.
    registration.update_browser(task['key'],data)
    return '已确认商号及续期日 '+value+'；'+address_note


def accept(task,payload,checked_at=None):
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
    if normalized(name)!=normalized(task['name']) and not (task.get('search_only') and compatible(name,row)):raise ValueError('摘要名称与任务不符')
    numbers=re.findall(r'ABN\s*:\s*([\d ]{11,15})',text,re.I)
    abns={re.sub(r'\D','',n) for n in numbers if valid_abn(re.sub(r'\D','',n))}
    if kind=='business':return accept_business_name(task,current,field,abns,url,checked_at)
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
        address_evidence=''
        name_matches=compatible(name,row) or any(score>=0.82 and n==name for score,n in rank_names(info,row))
        if name_matches and not same_address(address,row['address']):address_evidence=website_address(row,address)
        matched=matched or (name_matches and (same_address(address,row['address']) or bool(address_evidence)))
        raw=field('Renewal date') or field('Next renewal date');date_field='business_name_renewal_date';source_field='renewal_source'
    else:
        acn=re.sub(r'\D','',field('ACN'))
        if acn!=info['acn'] or (task['acn'] and acn!=task['acn']):raise ValueError('ACN 不一致')
        address=field('Locality of registered office')
        # Company locality alone never establishes a shop relationship.
        raw=field('Next review date');date_field='company_next_review_date';source_field='review_source'
    candidate_date=''
    if raw:
        try:candidate_date=datetime.strptime(raw,'%d/%m/%Y').date().isoformat()
        except ValueError:raise ValueError('官方日期格式无法识别')
    if not matched:
        if current.get('identity_status') not in ('自动匹配','人工核实'):
            data={}
            data.update(candidate_business_name=name,candidate_abn=abn,candidate_renewal_date=candidate_date if kind=='business' else '',candidate_source=url,match_status='高匹配候选',asic_status=field('Status'))
            registration.save_browser(task['key'],data)
        return '已读取官方摘要；门店关联仍不足，已保存证据但未确认主体'
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
        data['asic_status']=field('Status');data['match_status']='自动确认'
        data['renewal_checked_at']=checked_at or datetime.now(timezone.utc).isoformat()
        for field_name in ('candidate_business_name','candidate_abn','candidate_renewal_date','candidate_source'):data[field_name]=''
        if not previously_matched:
            data['identity_evidence']='ASIC 名称／唯一对应注册主体与门店名称对应，Principal place of business 地址：'+address+'；'+(address_evidence or '门店目录全地址一致')+'；ABN 当前持有人交叉核对。'+url
    data[date_field]=value;data[source_field]=url
    registration.save_browser(task['key'],data)
    return '已自动确认并保存日期 '+value


def inspect_screenshot(payload):
    with LOCK:
        task=next((t for t in STATE['tasks'] if t['id']==payload.get('id')),None)
        if not task or task['state']!='running':raise ValueError('截图任务已过期')
    url=urlsplit(payload.get('url',''))
    if url.scheme!='https' or url.hostname!='connectonline.asic.gov.au':raise ValueError('只能识别 ASIC 官方页')
    from ocr_fallback import extract
    folder=registration.PATH.parent/'asic_browser_evidence'
    identifier=task['id']+'-ocr-'+str(time.time_ns())
    extracted=extract(payload.get('screenshot'),folder,identifier)
    (folder/(identifier+'.json')).write_text(json.dumps(extracted,ensure_ascii=False,indent=2),encoding='utf-8')
    return extracted


def result(payload):
    with LOCK:
        task=next((t for t in STATE['tasks'] if t['id']==payload.get('id')),None)
        if not task or task['state']!='running':raise ValueError('任务已过期或尚未领取')
    payload=dict(payload)
    if payload.get('screenshot'):
        from ocr_fallback import extract
        image=payload.pop('screenshot')
        try:
            ocr=extract(image,registration.PATH.parent/'asic_browser_evidence',task['id'])
            payload.update(text=ocr.pop('text'),ocr=ocr)
            observed=ocr.get('rows',[])
            matching=[r for r in observed if compatible(r.get('name',''),task['row'])]
            if ocr.get('page_type')=='results' and matching and all(r.get('status','').casefold()=='cancelled' for r in matching):
                payload.update(error='截图中匹配商号为 Cancelled；分页范围未核实，保留观察记录',candidates=matching)
                registration.update_browser(task['key'],{'asic_observed_records':json.dumps(matching,ensure_ascii=False)})
        except ValueError as exc:payload['error']=str(exc)
    evidence={'task':task,'result':payload,'received_at':datetime.now(timezone.utc).isoformat()}
    if payload.get('error'):message='浏览器未完成：'+str(payload['error'])[:300]
    else:
        try:message=accept(task,payload)
        except Exception as exc:message='未采纳：'+type(exc).__name__+' '+str(exc)[:250]
    if not payload.get('error') and not message.startswith('未采纳') and task['kind']=='business':
        observed=payload.get('text','')
        name_match=re.search(r'(?:^|\n)\s*Business name\s*:\s*([^\n]+)',observed,re.I)
        ids={re.sub(r'\D','',v) for v in re.findall(r'ABN\s*:\s*([\d ]{11,15})',observed,re.I)}
        ids={v for v in ids if valid_abn(v)}
        if name_match and len(ids)==1:
            with LOCK:
                path=registration.PATH.parent/'asic_cache.json'
                cache=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
                cache[next(iter(ids))+'|'+normalized(name_match.group(1))]={'fetched_at':time.time(),'payload':payload}
                path.write_text(json.dumps(cache,ensure_ascii=False,indent=2),encoding='utf-8')
    current=registration.apply([task['row']])[0]
    if current.get('identity_status') not in ('人工核实','存在冲突'):
        cancelled='仅找到已注销' in message or '指定商号已注销' in message
        if cancelled:
            registration.update_browser(task['key'],{'asic_status':'Cancelled','auto_reason':message,'asic_observed_records':json.dumps(payload.get('candidates') or [{'text':payload.get('text','')}],ensure_ascii=False)})
        status=('已注销／跳过' if cancelled else
                '商号及续期日已确认' if message.startswith('已确认商号及续期日') else
                'ASIC 技术故障/待重试' if '技术故障' in message else
                '自动确认' if current.get('identity_status')=='自动匹配' else
                '多主体歧义' if '歧义' in message else
                '访问受限/待重试' if any(v in message for v in ('超时','超过','验证','读取失败','未采纳','未返回')) else
                '高匹配候选' if current.get('candidate_business_name') else '未找到有效记录')
        registration.update_browser(task['key'],{'match_status':status,'browser_last_result':message,'browser_checked_at':datetime.now(timezone.utc).isoformat()})
    folder=registration.PATH.parent/'asic_browser_evidence';folder.mkdir(parents=True,exist_ok=True)
    evidence['outcome']=message
    (folder/(task['id']+'.json')).write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    with LOCK:
        if task.get('alternatives') and any(reason in message for reason in ('没有结果','未找到对应','官方状态不是 Registered')):
            alternative,*remaining=task['alternatives']
            if not any(t['abn']==task['abn'] and t['name']==alternative for t in STATE['tasks']):
                follow={**task,'id':secrets.token_hex(12),'name':alternative,'alternatives':remaining,'state':'queued','url':'https://connectonline.asic.gov.au/RegistrySearch/faces/landing/panelSearch.jspx?'+urlencode({'searchTab':'bnsearch','searchName':alternative,'searchType':'Bn'})}
                follow.pop('started_at',None)
                STATE['tasks'].insert(STATE['tasks'].index(task)+1,follow)
        task['state']='done';STATE['results'].append({'name':task['row']['name'],'message':message,'seconds':round(time.time()-task.get('started_at',time.time()),2),'cached':False,'metrics':payload.get('metrics',{})})
    checkpoint();export_progress()
    return message


def stop():
    with LOCK:
        STATE.update(state='stopped',message='浏览器队列已停止；已保存的结果保留')
        checkpoint();export_progress()


def pause(message='插件已暂停', reason='manual'):
    with LOCK:
        if STATE['state'] not in ('done','stopped','idle'):
            STATE.update(state='paused',message=message,pause_reason=reason)
            checkpoint()


def resume(automatic=False):
    with LOCK:
        if automatic and (STATE['state']!='paused' or STATE.get('pause_reason')!='verification'):return False
        if STATE['state']=='paused':
            for task in STATE['tasks']:
                if task['state']=='running':task['started_at']=time.time()
            STATE.update(state='ready',message='插件继续查询',pause_reason=None)
            checkpoint()
            return True
        return False
