"""Postcode index of the official public ABN snapshot; no scraping or inferred names."""
import json, sqlite3, zipfile, time
from contextlib import closing
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parent.parent/'output'/'abn_data'
DB=ROOT/'abn.sqlite'
SOURCE='https://abr.business.gov.au/Tools/BulkExtract'

def parse_entity(e):
    abn=e.find('ABN')
    if abn is None or abn.get('status')!='ACT' or e.get('replaced','N')=='Y':return None
    main=e.find('MainEntity')
    if main is None:main=e.find('LegalEntity')
    if main is None:return None
    legal=main.findtext('NonIndividualName/NonIndividualNameText','').strip()
    if not legal:legal=' '.join(x.text or '' for x in main.findall('IndividualName/*') if x.tag!='NameTitle').strip()
    names=list(dict.fromkeys(n.findtext('NonIndividualNameText','').strip() for n in e.findall('OtherEntity/NonIndividualName') if n.get('type')=='BN' and n.findtext('NonIndividualNameText','').strip()))
    number=e.find('ASICNumber');acn=''
    if number is not None and number.get('ASICNumberType')=='ACN':acn=(number.text or '').strip()
    return {'abn':(abn.text or '').strip(),'legal_name':legal,'business_names':names,'acn':acn,
        'state':main.findtext('BusinessAddress/AddressDetails/State','').strip(),
        'postcode':main.findtext('BusinessAddress/AddressDetails/Postcode','').strip(),
        'asic_number':(number.text or '').strip() if number is not None else '',
        'asic_number_type':number.get('ASICNumberType','') if number is not None else '',
        'entity_type':e.findtext('EntityType/EntityTypeText',''),'status_from':abn.get('ABNStatusFromDate','')}

def build_index(folder=ROOT):
    folder=Path(folder);started=time.monotonic();package=json.loads((folder/'package.json').read_text())['result']
    resources=[r for r in package['resources'] if r['format']=='ZIP']
    if len(resources)!=2:raise ValueError('官方资源应包含两个压缩包；未更新索引')
    temp=folder/'abn.build.sqlite'
    if temp.exists():temp.unlink()
    db=sqlite3.connect(temp)
    try:
        db.execute('create table entities (abn text primary key, state text, postcode text, payload text)')
        count=0;files=[];extract_times=[]
        for r in resources:
            path=folder/r['url'].rsplit('/',1)[-1]
            with zipfile.ZipFile(path) as z:
                for name in z.namelist():
                    if not name.lower().endswith('.xml'):continue
                    files.append(name);batch=[]
                    with z.open(name) as f:
                        iterator=ET.iterparse(f,events=('start','end'));_,root=next(iterator)
                        for event,e in iterator:
                            if event!='end':continue
                            if e.tag=='ExtractTime' and e.text:extract_times.append(e.text)
                            if e.tag!='ABR':continue
                            row=parse_entity(e)
                            if row and row['abn'] and row['legal_name']:
                                batch.append((row['abn'],row['state'],row['postcode'],json.dumps(row,ensure_ascii=False)));count+=1
                            e.clear();root.clear()
                            if len(batch)>=10000:db.executemany('insert into entities values (?,?,?,?)',batch);batch=[]
                        db.executemany('insert into entities values (?,?,?,?)',batch)
                    db.commit();print('Indexed',name,count,flush=True)
        if len(files)!=20:raise ValueError('官方20个分片不完整；旧索引保留')
        db.execute('create index postcode_lookup on entities(state,postcode,abn)')
        meta={'source':SOURCE,'extract_times':sorted(set(extract_times)),'resources':[{k:r.get(k) for k in ('url','last_modified','size')} for r in resources], 'active_entities':count,'files':files,'index_seconds':round(time.monotonic()-started,2),'built_at':datetime.now(timezone.utc).isoformat()}
        db.execute('create table metadata(value text)');db.execute('insert into metadata values (?)',(json.dumps(meta),));db.commit()
    finally:
        db.close()
    temp.replace(folder/'abn.sqlite')
    (folder/'index_metrics.json').write_text(json.dumps(meta,indent=2));return meta

def company_acn(entity):
    if entity.get('acn'):return entity['acn']
    # Bulk XML labels ASIC numbers "undetermined". Classify only Australian
    # private/public companies, with a valid number matching this ABN suffix.
    number=entity.get('asic_number','')
    if entity.get('asic_number_type') not in ('','undetermined'):return ''
    if entity.get('entity_type') not in ('Australian Private Company','Australian Public Company'):return ''
    if len(number)!=9 or not number.isascii() or not number.isdigit() or not entity['abn'].endswith(number):return ''
    check=(10-sum(int(n)*w for n,w in zip(number[:8],range(8,0,-1)))%10)%10
    return number if check==int(number[-1]) else ''


def query(postcode,state,limit=20,offset=0,db_path=None):
    path=Path(db_path or DB)
    if not path.exists():raise ValueError('ABN 官方数据尚未建立本机索引，请先运行 ABN 数据更新脚本')
    with closing(sqlite3.connect(path)) as db:
        meta=json.loads(db.execute('select value from metadata').fetchone()[0])
        total=db.execute('select count(*) from entities where state=? and postcode=?',(state,postcode)).fetchone()[0]
        entities=[json.loads(r[0]) for r in db.execute('select payload from entities where state=? and postcode=? order by abn limit ? offset ?',(state,postcode,limit,offset))]
    rows=[]
    for e in entities:
        # Separate names so each registered business name can have its own renewal lookup.
        for name in e['business_names'] or ['']:
            rows.append({'name':name or e['legal_name'],'business_name':name,'legal_name':e['legal_name'],'abn':e['abn'],'acn':company_acn(e),
            'asic_number':e.get('asic_number',''),'asic_number_type':e.get('asic_number_type',''),'category':e['entity_type'],'state':state,'postcode':postcode,'requested_postcode':postcode,'location_scope':'ABN主要经营地点邮编',
            'address':state+' '+postcode+' · ABN '+e['abn'],'website':'','phone':'','email':'','contact_page':'','observed':'官方公开数据未提供网站和电话','opportunity':'',
            'status':'Active ABN（官方快照）','registration_status':'Active from '+(e['status_from'][:4]+'-'+e['status_from'][4:6]+'-'+e['status_from'][6:] if len(e['status_from'])==8 and e['status_from'].isdigit() else e['status_from']),'identity_status':'官方登记数据','identity_source':SOURCE,
            'source':'https://abr.business.gov.au/ABN/View?id='+e['abn'],'contact_source':SOURCE,'source_provider':'abn_bulk','abn_snapshot_at':'; '.join(meta.get('extract_times',[])),
            'checked_at':meta.get('built_at',''),'notes':('ACN 依据：官方 ASIC 编号、澳大利亚公司类型及校验位。' if company_acn(e) and not e.get('acn') else '')+'按主要经营地点筛选，不代表全部门店；ABN Active 不等于商号 Registered。'+('' if name else '未提供 Business Name，无法直接查商号续期日。'),
            'follow_up':'未联系'})
    return rows,{'provider':'abn','postcode':postcode,'state':state,'returned':len(rows),'returned_entities':len(entities),'total_active_entities':total,'offset':offset,'next_offset':offset+len(entities),'has_more':offset+len(entities)<total,'status':'finished','errors':[],'scope':'官方快照中主要经营地点对应邮编的 Active ABN；不代表全部门店','complete_area_census':False,'snapshot':meta}

if __name__=='__main__':print(json.dumps(build_index(),ensure_ascii=False))
