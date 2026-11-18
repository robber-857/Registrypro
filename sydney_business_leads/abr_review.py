import os

from abr_api import ApiClient
from local_leads import OUTPUT
import registration


def configured_guid():
    guid = os.environ.get('ABR_GUID','').strip()
    config = OUTPUT.parent/'abr_guid.local'
    return guid or (config.read_text(encoding='utf-8').strip() if config.is_file() else '')


def missing_abns(rows):
    return list(dict.fromkeys(r['abn'] for r in registration.apply(rows)
                              if r.get('source_provider')=='abn_bulk' and r.get('abn') and not r.get('business_name')))


def review(rows, targets, guid, cancelled, progress):
    checked = {}
    with ApiClient(guid, OUTPUT.parent/'web_cache'/'abr_lookup.sqlite', refresh=True) as client:
        for abn in targets:
            if cancelled(): raise InterruptedError('已暂停商号复核')
            progress(f'官方 API 正在复核 {len(checked)+1}/{len(targets)} 个 ABN')
            checked[abn] = client.lookup(abn)
    updated = []
    added = 0
    for row in rows:
        detail = checked.get(row.get('abn'))
        if not detail or row.get('business_name'):
            updated.append(row)
            continue
        names = detail['business_names'] if detail['status'].lower() == 'active' else []
        note = f"ABN Lookup API {detail['retrieved_at']} · {detail['status']} · 当前商号 {len(detail['business_names'])} 个"
        for name in names or ['']:
            updated.append({**row, 'name': name or row['name'], 'business_name': name,
                            'notes': row.get('notes','').replace('未提供 Business Name，无法直接查商号续期日。','') + ' ' + note})
        added += len(names)
    message = f'官方 API 已复核 {len(checked)} 个 ABN，补入 {added} 个当前商号'
    return updated, message
