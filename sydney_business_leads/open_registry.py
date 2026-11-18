"""Official business-name snapshot preflight. No renewal date is inferred."""
import csv, io, json, re, sqlite3, zipfile
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parent.parent/'output'/'open_data'
DB=ROOT/'names.sqlite'
def norm(value):return re.sub(r'[^a-z0-9]','',value.lower())
def build_index(folder=ROOT):
    folder=Path(folder);package=json.loads((folder/'package.json').read_text(encoding='utf8'))['result']
    resource=next(r for r in package['resources'] if r['format']=='CSV')
    target=folder/'names.sqlite';temp=folder/'names.build.sqlite'
    if temp.exists():temp.unlink()
    db=sqlite3.connect(temp)
    db.execute('create table names (name text, normalized text, status text, abn text)')
    count=0
    with zipfile.ZipFile(folder/'business_names.zip') as z:
        with z.open(next(n for n in z.namelist() if n.lower().endswith('.csv'))) as raw:
            reader=csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline=''),delimiter='\t')
            fields=reader.fieldnames
            if not {'BN_NAME','BN_STATUS','BN_ABN'}<=set(fields):raise ValueError('官方文件缺少必需字段')
            batch=[]
            for r in reader:
                if not r.get('BN_NAME'):continue
                name=r['BN_NAME'].strip();batch.append((name,norm(name),r['BN_STATUS'].strip(),(r.get('BN_ABN') or '').replace(' ','')))
                if len(batch)>=10000:db.executemany('insert into names values (?,?,?,?)',batch);count+=len(batch);batch=[]
            db.executemany('insert into names values (?,?,?,?)',batch);count+=len(batch)
    db.execute('create index name_lookup on names(normalized)')
    metadata={'source':resource['url'],'snapshot_at':resource['last_modified'],'fields':fields,'has_renewal_date':'BN_RENEW_DT' in fields,'count':count}
    db.execute('create table metadata (value text)');db.execute('insert into metadata values (?)',(json.dumps(metadata),));db.commit();db.close();temp.replace(target)
    (folder/'verified_schema.json').write_text(json.dumps(metadata,indent=2),encoding='utf8');return metadata

def lookup(row, aliases, known=None, db_path=None):
    path=Path(db_path or DB)
    if not path.exists():return {'outcome':'unavailable','reason':'开放数据索引未安装，插件补查'}
    try:
        with sqlite3.connect(path) as db:
            db.row_factory=sqlite3.Row
            meta=json.loads(db.execute('select value from metadata').fetchone()[0])
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(meta['snapshot_at']).replace(tzinfo=timezone.utc)).total_seconds()/86400
            if age<0 or age>14:return {**meta,'outcome':'stale','reason':'开放数据快照超过14天或日期异常，插件补查'}
            keys={norm(row['name'])}|set(aliases)
            if (known or {}).get('business_name'):keys.add(norm(known['business_name']))
            records=[dict(r) for r in db.execute('select distinct name,status,abn from names where normalized in ('+','.join('?' for _ in keys)+')',sorted(keys))]
        exact=[r for r in records if norm(r['name'])==norm(row['name'])]
        matches=exact or records
        current=[r for r in matches if r['status'].casefold()=='registered']
        outcome='missing';reason='开放数据无精确商号匹配，插件补查'
        if current:
            if len(current)==1:outcome='registered';reason='开放数据匹配当前商号，但不含续期日期，插件补查'
            else:outcome='ambiguous';reason='开放数据存在多个当前商号，保留候选并由插件核验'
        elif matches and all(r['status'].casefold()=='cancelled' for r in matches):outcome='cancelled';reason='官方开放数据匹配商号为 Cancelled，已记录并跳过'
        return {**meta,'outcome':outcome,'reason':reason,'records':matches,'selected':current[0] if len(current)==1 else None}
    except (OSError,ValueError,sqlite3.Error,KeyError) as exc:return {'outcome':'unavailable','reason':'开放数据读取失败，插件补查：'+type(exc).__name__}

if __name__=='__main__':print(json.dumps(build_index(),ensure_ascii=False,indent=2))
