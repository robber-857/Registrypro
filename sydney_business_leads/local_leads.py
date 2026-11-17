"""Public business directory discovery, independent of ABN Lookup credentials.

Uses Bing Maps public listing pages (not a guaranteed API). Never bypasses a
challenge. Exact address postcode/state filtering; directory contacts remain
labelled as such. Standard library only, Python 3.10+.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import ipaddress
import json
import re
import socket
import time
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
import registration

HERE = Path(__file__).resolve().parent
OUTPUT = HERE.parent / 'output' / 'runs'
CACHE = HERE.parent / 'output' / 'web_cache'
STATES = {'NSW':'New South Wales','VIC':'Victoria','QLD':'Queensland','ACT':'Australian Capital Territory',
          'SA':'South Australia','WA':'Western Australia','TAS':'Tasmania','NT':'Northern Territory'}
CATEGORIES = {'餐饮':'restaurants', '咖啡店':'cafes', '美容美发':'hair beauty salons',
              '医疗健康':'dentists physiotherapists', '零售':'shops', '专业服务':'accountants real estate agencies'}
FIELDS = [('name','商家名称'),('category','行业'),('postcode','实际邮编'),('requested_postcode','搜索邮编'),('location_scope','地区范围'),('state','州'),
          ('address','营业地址'),('website','官网或目录所列网站'),('phone','公开电话'),
          ('email','公开邮箱'),('contact_page','联系页面'),('observed','页面观察'),
          ('opportunity','可能需要的服务（待确认）'),('status','核查状态'),
          ('source','目录或原始来源'),('contact_source','联系方式来源'),('checked_at','核查日期'),
          ('notes','备注'),('abn','ABN'),('follow_up','跟进状态')]
FIELDS += registration.FIELDS


class CollectionError(Exception):
    pass


def validate(postcode, state, limit):
    if not re.fullmatch(r'[0-9]{4}', str(postcode)):
        raise CollectionError('请输入四位澳洲邮编，例如 2134。')
    if state not in STATES:
        raise CollectionError('请选择有效的州。')
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise CollectionError('收集上限必须在 1–100 之间。')


def public_url(url):
    """Prevent scraped links from reaching loopback/private network endpoints."""
    p = urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise CollectionError('不支持的网站地址。')
    if p.port not in (None,80,443):
        raise CollectionError('仅支持标准网页端口。')
    addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80))
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise CollectionError('跳过非公开网络地址。')
    return url


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, refresh=False, ttl=86400):
    public_url(url)
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (hashlib.sha256(url.encode()).hexdigest()+'.json')
    if path.exists() and not refresh:
        try:
            c = json.loads(path.read_text(encoding='utf-8'))
            if time.time()-c['time'] < ttl:
                return c['text'], c['url'], True
        except (OSError, ValueError, KeyError):
            pass
    request = Request(url, headers={'User-Agent':'Mozilla/5.0 (compatible; LocalBusinessResearch/1.0)',
                                    'Accept-Language':'en-AU,en;q=0.8'})
    try:
        with build_opener(PublicRedirect()).open(request, timeout=18) as r:
            if not any(t in r.headers.get('Content-Type','') for t in ('text/','application/xhtml')):
                raise CollectionError('返回内容不是网页。')
            raw = r.read(3_000_001)
            if len(raw)>3_000_000:
                raise CollectionError('网页超过 3 MB，本次跳过。')
            text = raw.decode(r.headers.get_content_charset() or 'utf-8',errors='replace')
            final = r.url
    except HTTPError as e:
        raise CollectionError(f'网页返回 HTTP {e.code}，未绕过访问限制。') from e
    c = {'url':final,'text':text,'time':time.time()}
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=CACHE,suffix='.tmp',delete=False) as handle:
        handle.write(json.dumps(c,ensure_ascii=False))
        temp=Path(handle.name)
    temp.replace(path)
    return text, final, False


class Listings(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items=[]
    def handle_starttag(self, tag, attrs):
        value=dict(attrs).get('data-entity')
        if value:
            try:
                item=json.loads(value).get('entity',{})
                if item.get('title') and item.get('address'):
                    self.items.append(item)
            except (ValueError, TypeError):
                pass


def parse_listings(text):
    p=Listings(); p.feed(text)
    if not p.items:
        raise CollectionError('目录未返回可解析的商家：可能没有结果、页面改版或触发访问限制；不表示该地区没有企业。')
    return p.items


def address_matches(address, postcode, state):
    """Only the explicitly listed postal address qualifies; no radius inference."""
    return bool(re.search(r'\b'+re.escape(postcode)+r'\b', address) and
                re.search(r'\b(?:'+re.escape(state)+'|'+re.escape(STATES[state])+r')\b',address,re.I))


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[]; self.links=[]; self.hidden=0
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag in ('script','style','noscript'):
            self.hidden+=1
        if not self.hidden and tag=='a' and a.get('href'):
            self.links.append(a['href'])
        if not self.hidden and tag in ('br','p','div','li','h1','h2','h3','footer'):
            self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script','style','noscript') and self.hidden:
            self.hidden-=1
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)
    @property
    def text(self):
        return ' '.join(self.parts)


def extract_contacts(text):
    """Only shared organizational mailboxes, never inferred named-person emails."""
    emails=set(re.findall(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b',text))
    generic=re.compile(r'^(info|contact|hello|enquir(?:y|ies)|admin|reception|office|sales|support|bookings?|reservations?|customer[._-]?service|burwood|hair)([._-].*|\d*)$',re.I)
    emails=sorted(e for e in emails if generic.fullmatch(e.split('@')[0]))
    phones=set(re.findall(r'(?<!\d)(?:\+61\s?[23478]|\(?0[23478]\)?)\s?\d{4}\s?\d{4}(?!\d)|(?<!\d)04\d{2}\s?\d{3}\s?\d{3}(?!\d)',text))
    return emails, sorted(phones)


def enrich(row, refresh=False):
    row=dict(row)
    if not row['website']:
        row['status']='仅目录资料；未提供网站'
        row['observed']='目录未提供网站，不能据此断定没有官网'
        row['opportunity']='先确认官网及现有业务流程，再判断建站或自动化需求'
        return row
    try:
        html, final, cached=fetch(row['website'],refresh)
        page=Page(); page.feed(html)
        sources=[final]
        texts=[page.text]
        links=page.links[:]
        contacts=[]
        for href in links:
            url=urljoin(final,href)
            if (urlsplit(url).netloc==urlsplit(final).netloc and
                re.search(r'contact|location',urlsplit(url).path,re.I) and url not in contacts and url!=final):
                contacts.append(url)
        for url in contacts[:1]:
            try:
                h,f,_=fetch(url,refresh); p=Page(); p.feed(h)
                sources.append(f); texts.append(p.text); links.extend(p.links)
                row['contact_page']=f
            except Exception:
                row['notes']+=' 联系页面读取失败。'
        text='\n'.join(texts)
        emails,phones=extract_contacts(text)
        # A multi-branch page can mix unrelated contacts. Preserve listed branch
        # phone, and don't automatically choose among several mailbox addresses.
        row['email']=emails[0] if len(emails)==1 else ''
        if len(emails)>1:
            row['notes']+=' 页面有多个公共邮箱，未自动选择分店邮箱。'
        if len(phones)==1 and not row['phone']:
            row['phone']=phones[0]
        row['contact_source']='目录电话；网站公开邮箱（如有）：'+' | '.join(sources)
        row['status']='已读取网站；商家与联系方式匹配待复核'
        if cached:
            row['notes']+=' 官网使用 24 小时内缓存。'
        booking=any(re.search(r'book|appointment|reservation|fresha|hotdoc|healthengine|centaur|cliniko',x,re.I) for x in links)
        ordering=any(re.search(r'/shop|/order|/cart|ubereats|menulog',x,re.I) for x in links)
        row['observed']=('所查页面发现预约链接；尚未测试完成预约。' if booking else '所查页面未识别预约链接，不代表商家没有预约系统。')
        if ordering:
            row['observed']+=' 发现网店／订购链接。'
        row['opportunity']=('评估现有预约的询盘跟进、提醒与转化统计' if booking else '核实当前预约或询盘方式，评估在线表单与跟进自动化')
        if ordering:
            row['opportunity']+='；评估订单通知与客户回访'
        if not re.search(r'\b'+row['postcode']+r'\b',text):
            row['notes']+=' 所查网站文本未找到目标邮编，地区依据仍为目录地址。'
    except Exception as e:
        row['status']='仅目录资料；网站读取失败'
        row['notes']+=f' {type(e).__name__}: {str(e)[:160]}'
        row['observed']='网站本次未能读取，不能据此判定网站故障或不存在'
        row['opportunity']='先人工核实官网与业务流程，再讨论服务需求'
    return row


def enrich_registered(row,refresh=False):
    from auto_registration import investigate
    return investigate(enrich(row,refresh),refresh)


def csv_bytes(rows):
    buf=io.StringIO(newline=''); w=csv.writer(buf)
    w.writerow([v for _,v in FIELDS])
    for r in registration.dedupe(registration.apply(rows)):
        values=[]
        for key,_ in FIELDS:
            value=str(r.get(key,'') or '')
            if value.lstrip().startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n')):
                value="'"+value
            values.append(value)
        w.writerow(values)
    return b'\xef\xbb\xbf'+buf.getvalue().encode('utf-8')


def annotated_csv(data):
    """Download old snapshots with current annotations without rewriting historical files."""
    mapping={label:key for key,label in FIELDS}
    rows=[]
    for original in csv.DictReader(io.StringIO(data.decode('utf-8-sig'))):
        row={mapping[label]:value for label,value in original.items() if label in mapping}
        # Undo only this exporter's formula prefix before exporting once more.
        for key,value in row.items():
            if value.startswith("'") and (value[1:].lstrip().startswith(('=','+','-','@')) or value[1:].startswith(('\t','\r','\n'))):
                row[key]=value[1:]
        rows.append(row)
    return csv_bytes(rows)


def collect(postcode, state='NSW', limit=20, categories=None, refresh=False, progress=None, cancelled=None, include_nearby=True, provider="bing"):
    validate(postcode,state,limit)
    if provider not in ('google','bing'):raise CollectionError('数据源不正确')
    if provider=='google':
        from google_places import configured
        if not configured():raise CollectionError('Google Maps 尚未配置 GOOGLE_PLACES_API_KEY；未回退 Bing。')
    tokens={};ended=set()
    cats=categories or list(CATEGORIES)
    if not cats or any(c not in CATEGORIES for c in cats):
        raise CollectionError('请选择列表中的行业。')
    tell=progress or (lambda message,rows:None)
    cancelled=cancelled or (lambda:False)
    rows=[]; seen=set(); errors=[]; directory_requests=[]; excluded=0
    for page_no in range(3 if provider=="google" else 2):
        for cat in cats:
            if cat in ended:continue
            if cancelled() or len(rows)>=limit:
                break
            tell(f'正在搜索 {state} {postcode} · {cat}（第 {page_no+1} 页）',rows)
            url='https://www.bing.com/maps/overlaybfpr?'+urlencode({
                'q':f'{CATEGORIES[cat]} near {state} {postcode} Australia',
                'count':30,'first':page_no*30,'setlang':'en-US','cc':'AU'})
            try:
                if provider=='google':
                    from google_places import search_page,ENDPOINT
                    items,tokens[cat]=search_page(f'{CATEGORIES[cat]} near {state} {postcode} Australia',tokens.get(cat,''))
                    source,cached=ENDPOINT,False
                    if not tokens[cat]:ended.add(cat)
                else:
                    text,source,cached=fetch(url,refresh,ttl=3600)
                    items=parse_listings(text)
                directory_requests.append({'url':source,'cached':cached,'provider':provider})
            except Exception as e:
                errors.append(f'{cat} 第 {page_no+1} 页：{e}')
                continue
            added=0
            for item in items:
                actual = re.search(r'\b([0-9]{4})\s*$',item['address'])
                actual_postcode = actual.group(1) if actual else ''
                target_match = address_matches(item['address'],postcode,state)
                state_match = bool(re.search(r'\b(?:'+re.escape(state)+'|'+re.escape(STATES[state])+r')\b',item['address'],re.I))
                if not state_match or not actual_postcode or (not include_nearby and not target_match):
                    excluded+=1; continue
                key=(re.sub(r'\W','',item['title']).casefold(),re.sub(r'\W','',item['address']).casefold())
                if key in seen:
                    continue
                seen.add(key)
                ident=item.get('id','').removeprefix('ypid:')
                rows.append({'id':len(rows)+1,'name':item['title'],'category':item.get('primaryCategoryName') or cat,
                    'postcode':actual_postcode,'requested_postcode':postcode,'location_scope':'目标邮编' if target_match else '邻区候选（搜索目录返回，距离未核实）','state':state,'address':item['address'],'website':item.get('website') or '',
                    'phone':item.get('phone') or '','email':'','contact_page':'','observed':'','opportunity':'',
                    'status':'仅目录资料，待复核','source':item.get('source') or ('https://www.bing.com/maps?'+urlencode({'ss':'ypid.'+ident}) if ident and provider=='bing' else source),
                    'contact_source':('Google Maps / Places' if provider=='google' else 'Bing Maps')+' 商家目录（未电话验证）','checked_at':datetime.now(timezone.utc).isoformat(),
                    'notes':'目录可能过期；服务建议待商家确认。','abn':'','follow_up':'未联系'})
                added+=1
                if len(rows)>=limit or (page_no==0 and added>=max(1,limit//len(cats))):
                    break
            time.sleep(0.4)
        if cancelled() or len(rows)>=limit:
            break
    if not rows and errors:
        raise CollectionError('本次没有取得可用结果。'+ ' / '.join(errors))
    if not cancelled():
        with ThreadPoolExecutor(max_workers=4) as pool:
            tasks={pool.submit(enrich_registered,r,refresh):i for i,r in enumerate(rows)}
            for number,task in enumerate(as_completed(tasks),1):
                i=tasks[task]; rows[i]=task.result()
                tell(f'已核查 {number}/{len(rows)} 个网站 · {rows[i]["name"]}',rows)
                if cancelled():
                    for t in tasks: t.cancel()
                    break
    meta={'postcode':postcode,'state':state,'requested_limit':limit,'returned':len(rows),'categories':cats,
          'provider':provider,'status':'cancelled' if cancelled() else ('partial' if errors else 'finished'),
          'target_reached':len(rows)>=limit,'complete_area_census':False,'include_nearby':include_nearby,'errors':errors,
          'excluded_outside_postcode_or_state':excluded,'requests':directory_requests,
          'scope':'Public directory candidates; not an ABN or ASIC census. Websites and contacts require review.',
          'finished_at':datetime.now(timezone.utc).isoformat()}
    return rows,meta


def save_run(rows,meta):
    OUTPUT.mkdir(parents=True,exist_ok=True)
    name=f"leads_{meta['state']}_{meta['postcode']}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}"
    path=OUTPUT/(name+'.csv'); path.write_bytes(csv_bytes(rows))
    (OUTPUT/(name+'.json')).write_text(json.dumps({'rows':rows,'metadata':meta},ensure_ascii=False,indent=2),encoding='utf-8')
    return name


def main():
    p=argparse.ArgumentParser(description='无需 GUID 的公开商家候选名单收集')
    p.add_argument('--postcode',required=True); p.add_argument('--state',default='NSW',choices=STATES)
    p.add_argument('--provider',choices=['bing','google'],default='bing')
    p.add_argument('--limit',type=int,default=20); p.add_argument('--refresh',action='store_true')
    p.add_argument('--strict-postcode',action='store_true',help='仅保留目标邮编，默认也保留目录返回的同州邻区候选')
    a=p.parse_args()
    try:
        rows,meta=collect(a.postcode,a.state,a.limit,refresh=a.refresh,include_nearby=not a.strict_postcode,provider=a.provider,progress=lambda s,r:print(s,flush=True))
        print(OUTPUT/(save_run(rows,meta)+'.csv'))
        print(f"实际取得 {len(rows)} 条；状态 {meta['status']}；不代表地区全部企业。")
        return 0 if meta['status']=='finished' else 2
    except CollectionError as e:
        print(str(e)); return 2


if __name__=='__main__':
    raise SystemExit(main())
