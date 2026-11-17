"""Evidence-driven registration research using public pages; no fuzzy identity promotion."""
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlencode
import registration


def normalized(text):
    return re.sub(r'[^a-z0-9]', '', text.casefold())


def valid_abn(value):
    if not re.fullmatch(r'[0-9]{11}',value): return False
    numbers=list(map(int,value));numbers[0]-=1
    return sum(x*w for x,w in zip(numbers,(10,1,3,5,7,9,11,13,15,17,19)))%89==0


class Tables(HTMLParser):
    def __init__(self):
        super().__init__();self.tables=[];self.table=None;self.row=None;self.cell=None;self.href='';self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='table': self.table=[]
        if tag=='tr': self.row=[]
        if tag in ('th','td'): self.cell=[]
        if tag=='a': self.href=dict(attrs).get('href','');self.anchor=[]
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
        if self.href:self.anchor.append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.href:
            self.links.append((' '.join(' '.join(self.anchor).split()),self.href));self.href=''
        if tag in ('th','td') and self.cell is not None:
            if self.row is not None:self.row.append(' '.join(' '.join(self.cell).split()))
            self.cell=None
        if tag=='tr' and self.row is not None:
            if self.table is not None:self.table.append(self.row)
            self.row=None
        if tag=='table' and self.table is not None:self.tables.append(self.table);self.table=None


def parse_abr(html,abn):
    from local_leads import Page
    page=Page();page.feed(html)
    heading=re.search(r'Current details for ABN\s+([\d\s]{11,18})',page.text)
    if not heading or re.sub(r'\D','',heading.group(1))!=abn:
        raise ValueError('官方页面未返回所请求的 ABN 详情')
    parser=Tables();parser.feed(html)
    fields={cells[0].rstrip(':'):cells[1] for table in parser.tables for cells in table if len(cells)==2}
    if not fields.get('Entity name') or not fields.get('ABN status'):raise ValueError('官方详情格式无法识别')
    names=[]
    for table in parser.tables:
        for index,cells in enumerate(table):
            if cells and cells[0]=='Business name':
                names=[row[0] for row in table[index+1:] if row]
    links={name:url for name,url in parser.links if urlsplit(url).hostname=='connectonline.asic.gov.au'}
    number=re.search(r'ASIC registration - ACN or ARBN or ARSN or ARFN\s+(\d{3}\s+\d{3}\s+\d{3})',page.text)
    acn=re.sub(r'\s','',number.group(1)) if number and 'Company' in fields.get('Entity type','') else ''
    return {'legal_name':fields['Entity name'],'abn':abn,'acn':acn,'names':names,
            'registration_status':fields['ABN status'],'links':links,
            'company_link':links.get('View record on the ASIC website','')}


def date_from_asic(html,label,identifier):
    from local_leads import Page
    from datetime import datetime
    page=Page();page.feed(html);text=' '.join(page.text.split())
    # Must be a summary for the expected identifier, not a generic landing/help page.
    if normalized(identifier) not in normalized(text) or not re.search(r'Company Summary|Business Name Summary|Business name details',text,re.I):
        return ''
    match=re.search(re.escape(label)+r'\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})(?!\d)',text,re.I)
    return datetime.strptime(match.group(1),'%d/%m/%Y').date().isoformat() if match else ''


def investigate(row,refresh=False):
    from local_leads import fetch,Page
    out={k:'' for k,_ in registration.FIELDS}
    out.update(abn='',identity_status='自动未确认',auto_checked_at=datetime.now(timezone.utc).isoformat(),
               date_status='未取得：尚未确认注册主体')
    def finish(reason):
        out['auto_reason']=reason
        registration.save_auto(registration.key(row),out)
        return registration.apply([row])[0]
    previous=registration.apply([row])[0]
    if previous['identity_status'] in ('人工核实','存在冲突'):return previous
    if not refresh and previous.get('auto_checked_at'):
        try:
            if (datetime.now(timezone.utc)-datetime.fromisoformat(previous['auto_checked_at'])).total_seconds()<86400:return previous
        except ValueError:pass
    if not row.get('website'):return finish('目录没有提供网站，缺少可关联注册主体的证据')
    try:
        html,home,_=fetch(row['website'],refresh)
        page=Page();page.feed(html);pages=[(home,page)]
        links=[]
        for href in page.links:
            url=urljoin(home,href)
            if urlsplit(url).netloc==urlsplit(home).netloc and re.search(r'contact|location|privacy|terms|legal|about',urlsplit(url).path,re.I) and url!=home and url not in links:links.append(url)
        errors=[]
        for url in links[:3]:
            try:
                h,final,_=fetch(url,refresh)
                if urlsplit(final).netloc!=urlsplit(home).netloc:continue
                p=Page();p.feed(h);pages.append((final,p))
            except Exception as exc:errors.append(type(exc).__name__)
        ids={}
        for url,p in pages:
            for match in re.finditer(r'\bA\.?B\.?N\.?\s*[:#]?\s*((?:\d[ \t\r\n]*){11})(?!\d)',p.text,re.I):
                abn=re.sub(r'\D','',match.group(1))
                if valid_abn(abn):ids.setdefault(abn,[]).append(url)
        candidates=[]
        if len(ids)>1:
            for abn,sources in list(ids.items())[:6]:
                candidate={'abn':abn,'sources':sources}
                try:
                    detail,_,_=fetch('https://abr.business.gov.au/ABN/View?id='+abn,refresh,ttl=3600)
                    info=parse_abr(detail,abn)
                    candidate.update({k:info[k] for k in ('legal_name','names','registration_status')})
                except Exception as exc:candidate['error']=type(exc).__name__
                candidates.append(candidate)
            out['auto_candidates']=json.dumps(candidates,ensure_ascii=False)
            return finish('网站出现多个有效 ABN，未自动选择集团或分店主体')
        if not ids:
            search='https://abr.business.gov.au/Search/ResultsActive?'+urlencode({'SearchText':row['name']})
            h,_,_=fetch(search,refresh,ttl=3600)
            p=Page();p.feed(h)
            numbers=[]
            for href in p.links:
                match=re.search(r'/ABN/View\?(?:id|abn)=(\d{11})',href,re.I)
                if match and match.group(1) not in numbers:numbers.append(match.group(1))
            for number in numbers[:3]:
                source='https://abr.business.gov.au/ABN/View?id='+number
                detail,_,_=fetch(source,refresh,ttl=3600)
                info=parse_abr(detail,number)
                candidates.append({k:info[k] for k in ('legal_name','abn','names')})
            out['auto_candidates']=json.dumps(candidates,ensure_ascii=False)
            return finish(('网站未发现有效 ABN；已保存官方候选，不能仅凭同名确认。' if candidates else '网站未发现有效 ABN；官方搜索未返回可解析候选。')+('部分网站页面读取失败。' if errors else ''))
        abn=next(iter(ids));source='https://abr.business.gov.au/ABN/View?id='+abn
        detail,_,_=fetch(source,refresh,ttl=3600);info=parse_abr(detail,abn)
        names=[name for name in info['names'] if normalized(name)==normalized(row['name'])]
        legal_match=normalized(info['legal_name'])==normalized(row['name'])
        phone=re.sub(r'\D','',row.get('phone',''))
        location=any(re.search(r'\b'+re.escape(row.get('postcode',''))+r'\b',p.text) and len(phone)>=8 and phone[-8:] in re.sub(r'\D','',p.text) for _,p in pages)
        if not (len(names)==1 or legal_match) or not location or not info['registration_status'].startswith('Active'):
            out['auto_candidates']=json.dumps([{k:info[k] for k in ('legal_name','abn','names','registration_status')}],ensure_ascii=False)
            return finish('已找到官网 ABN 与官方记录，但名称精确对应、门店电话与邮编、或 Active 状态条件未全部满足')
        out.update({k:info[k] for k in ('legal_name','abn','acn','registration_status')})
        out.update(business_name=names[0] if names else '',identity_status='自动匹配',identity_source=source,
                   identity_checked_at=out['auto_checked_at'],identity_evidence='网站唯一有效 ABN：'+' | '.join(ids[abn])+'；官方当前登记名称精确对应目录名称；网站可见门店电话尾号与邮编一致。规则匹配，不等于法律确认。')
        date_notes=[]
        for field,label,url,identifier,source_field in (
            ('company_next_review_date','Next review date',info['company_link'],info['acn'],'review_source'),
            ('business_name_renewal_date','Next renewal date',info['links'].get(out['business_name'],''),out['business_name'],'renewal_source')):
            if not url or not identifier:date_notes.append(label+'：没有对应官方链接');continue
            try:
                html,final,_=fetch(url,refresh,ttl=3600)
                if urlsplit(final).hostname!='connectonline.asic.gov.au':raise ValueError('非预期官方记录地址')
                value=date_from_asic(html,label,identifier)
                if value:out[field]=value;out[source_field]=final
                else:date_notes.append(label+'：ASIC 页面需 JavaScript 或未返回可识别摘要，未取得日期')
            except Exception as exc:date_notes.append(label+'：读取失败 '+type(exc).__name__)
        out['date_status']='；'.join(date_notes) or '已读取对应 ASIC 摘要日期'
        return finish('网站 ABN、官方名称和门店联系方式通过规则匹配；未发送邮件')
    except Exception as exc:
        return finish('自动查询未完成：'+type(exc).__name__+' '+str(exc)[:180])
