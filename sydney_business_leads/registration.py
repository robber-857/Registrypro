"""Explicit, source-backed registration annotations; never infer a legal entity from a shop name."""
import hashlib
import json
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

FIELDS = [('business_name','Business Name（登记商业名称）'),
          ('legal_name','注册主体名称（Legal Name）'), ('acn','ACN'),
          ('business_name_renewal_date','Business Name 下次续期日期'),
          ('company_next_review_date','公司下次年度审核日期'),
          ('registration_status','登记状态（原文）'),
          ('identity_status','注册名称核验状态'), ('identity_source','名称核验来源'),
          ('renewal_source','续期日期来源'), ('review_source','年度审核日期来源'),
          ('identity_evidence','门店与注册主体关联依据'), ('identity_checked_at','注册资料核验时间')]
EDITABLE = {k for k,_ in FIELDS} - {'identity_checked_at'} | {'abn'}
LOCK = threading.RLock()
PATH = Path(__file__).resolve().parent.parent / 'output' / 'registration_annotations.json'


def key(row):
    values = [str(row.get(k, '')).strip().casefold() for k in ('name','address')]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode('utf-8')).hexdigest()


def load():
    return json.loads(PATH.read_text(encoding='utf-8')) if PATH.exists() else {}


def apply(rows):
    with LOCK:
        saved = load()
    result=[]
    for original in rows:
        row=dict(original)
        # A previously exported verification must not silently become a new verification.
        for field,_ in FIELDS:
            row.setdefault(field, '')
        row['identity_status'] = row.get('identity_status') or '待核实'
        row.update(saved.get(key(row), {}))
        row['registration_key'] = key(row)
        result.append(row)
    return result


def official_url(value):
    p=urlsplit(value)
    host=(p.hostname or '').lower()
    return p.scheme=='https' and not p.username and not p.password and p.port in (None,443) and (
        host=='asic.gov.au' or host.endswith('.asic.gov.au') or host=='abr.business.gov.au')


def checked(values):
    if not isinstance(values,dict) or set(values)-EDITABLE:
        raise ValueError('不支持的注册资料字段')
    data={}
    for field in EDITABLE:
        value=values.get(field,'')
        if not isinstance(value,str) or len(value)>4000:
            raise ValueError('字段格式不正确或内容过长')
        data[field]=value.strip()
    data['identity_status']=data['identity_status'] or '待核实'
    if data['identity_status'] not in ('待核实','人工核实','存在冲突'):
        raise ValueError('核验状态不正确')
    for field in ('business_name_renewal_date','company_next_review_date'):
        if data[field]:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',data[field]):
                raise ValueError('日期请使用 YYYY-MM-DD 格式')
            date.fromisoformat(data[field])
    for field in ('identity_source','renewal_source','review_source'):
        if data[field] and not official_url(data[field]):
            raise ValueError('注册资料来源请填写 ASIC 或 ABN Lookup 官方 HTTPS 链接')
    for field,length in (('abn',11),('acn',9)):
        data[field]=re.sub(r'\s','',data[field])
        if data[field] and not re.fullmatch(r'[0-9]{'+str(length)+'}',data[field]):
            raise ValueError(field.upper()+' 位数不正确')
    if data['abn']:
        digits=list(map(int,data['abn'])); digits[0]-=1
        if sum(x*w for x,w in zip(digits,(10,1,3,5,7,9,11,13,15,17,19)))%89:
            raise ValueError('ABN 校验位不正确，请核对原始资料')
    if data['acn'] and sum(int(n)*w for n,w in zip(data['acn'],(8,7,6,5,4,3,2,1,1)))%10:
        raise ValueError('ACN 校验位不正确，请核对原始资料')
    if data['identity_status']=='人工核实':
        if not (data['business_name'] or data['legal_name']) or not data['identity_source'] or not data['identity_evidence'] or not (data['abn'] or data['acn']):
            raise ValueError('人工核实需要登记名称、ABN/ACN、官方来源及门店关联依据；不能仅凭名称相似确认')
    for field,source in (('business_name_renewal_date','renewal_source'),('company_next_review_date','review_source')):
        if data[field] and (not data[source] or 'asic.gov.au' not in (urlsplit(data[source]).hostname or '')):
            raise ValueError('日期需要独立的 ASIC 来源；ABN Lookup 不用于证明续期或年度审核日期')
    if data['business_name_renewal_date'] and not data['business_name']:
        raise ValueError('续期日期需要对应的 Business Name')
    if data['company_next_review_date'] and not data['legal_name']:
        raise ValueError('年度审核日期需要对应的注册公司名称')
    data['identity_checked_at']=datetime.now(timezone.utc).isoformat() if data['identity_status']=='人工核实' else ''
    return data


def save(identifier,values):
    if not isinstance(identifier,str) or not re.fullmatch(r'[0-9a-f]{64}',identifier):
        raise ValueError('商家标识不正确')
    data=checked(values)
    with LOCK:
        saved=load(); saved[identifier]=data
        PATH.parent.mkdir(parents=True,exist_ok=True)
        temporary=PATH.with_suffix('.tmp')
        temporary.write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(PATH)
    return data
