"""Resumable, bounded public-directory pagination; never claims a complete census."""
import copy
import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from local_leads import (HERE, CATEGORIES, STATES, CollectionError, validate,
                         fetch, parse_listings, enrich_registered as enrich, csv_bytes, address_matches)

MAX_PAGES = 5  # Safety cap, not a statement about the provider's coverage.
DELAYS = (5, 15, 30, 60)


def options_checked(options):
    allowed = {'postcode', 'end_postcode', 'state', 'delay', 'categories', 'refresh', 'include_nearby', 'provider', 'use_asic'}
    if not isinstance(options, dict) or set(options) - allowed:
        raise ValueError('不支持的任务参数')
    options = dict(options)
    options.setdefault('provider','bing')
    if options['provider'] not in ('google','bing','abn'):raise ValueError('数据源不正确')
    if not isinstance(options.get('postcode'), str) or not isinstance(options.get('end_postcode', ''), str):
        raise ValueError('邮编必须是四位数字文本')
    validate(options.get('postcode'), options.get('state'), 20)
    end = options.get('end_postcode') or '9999'
    validate(end, options['state'], 20)
    if int(end) < int(options['postcode']):
        raise ValueError('结束邮编不能小于起始邮编')
    if type(options.get('delay')) is not int or options['delay'] not in DELAYS:
        raise ValueError('间隔只能选择 5、15、30 或 60 秒')
    if options['provider']=='abn':options['categories']=['Active ABN'];options['include_nearby']=False
    cats = options.get('categories')
    if not isinstance(cats, list) or not cats or (options['provider']!='abn' and any(c not in CATEGORIES for c in cats)):
        raise ValueError('请选择有效行业')
    for flag in ('refresh', 'include_nearby', 'use_asic'):
        if flag in options and not isinstance(options[flag], bool):
            raise ValueError('选项格式不正确')
        options.setdefault(flag, False)
    options['categories'] = list(dict.fromkeys(cats))
    if options['provider']=='google':
        from google_places import configured
        if not configured():raise ValueError('Google Maps 尚未配置 GOOGLE_PLACES_API_KEY；未启动任务，也未回退 Bing。')
    options['end_postcode'] = end
    return options


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)


class Automation:
    def __init__(self, folder=None):
        self.folder = Path(folder or HERE.parent / 'output' / 'automation')
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.data = {'state': 'idle', 'message': '连续邮编任务尚未启动', 'rows': [], 'history': []}
        checkpoint = self.folder / 'checkpoint.json'
        if checkpoint.exists():
            self.data = json.loads(checkpoint.read_text(encoding='utf-8'))
            if self.data['state'] != 'done':
                self.data.update(state='paused', message='已恢复上次进度，点击继续任务后运行。')

    def _checkpoint(self):
        atomic_write(self.folder / 'checkpoint.json', json.dumps(self.data, ensure_ascii=False).encode('utf-8'))

    def snapshot(self):
        with self.lock:
            return copy.deepcopy({k: v for k, v in self.data.items()
                                  if k not in ('pending', 'seen', 'category_seen')})

    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def _new_postcode(self, postcode):
        self.data.pop('asic_generation',None);self.data.pop('asic_batch',None)
        self.data.update(postcode=postcode, rows=[], pending=[], seen=[], category_seen=[],
                         category_index=0, page=0, reasons=[], requests=[], batches=0,
                         next_run_at=None, error='',google_token='')

    def start(self, options):
        options = options_checked(options)
        with self.lock:
            if self.running():
                raise ValueError('连续任务正在运行')
            if self.data['state'] == 'paused':
                raise ValueError('有已暂停的任务，请继续或结束该任务后再新建')
            self.data = {'state': 'paused', 'options': options, 'history': [],
                         'run_id': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:8]}
            self._new_postcode(options['postcode'])
            self.resume()

    def resume(self):
        with self.lock:
            if self.running() or self.data['state'] != 'paused':
                raise ValueError('没有可继续的暂停任务，或任务仍在运行')
            self.stop.clear()
            self.data.update(state='running', message='正在继续连续邮编任务', error='')
            self._checkpoint()
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def pause(self):
        self.stop.set()
        with self.lock:
            if self.running():
                self.data.update(message='正在暂停；等待当前网络请求完成后保存进度…')

    def finish(self):
        with self.lock:
            if self.running():
                raise ValueError('请先暂停，等待保存完成后结束任务')
            if self.data['state'] != 'paused':
                raise ValueError('没有暂停的任务')
            self._export('用户结束（未完成当前邮编）')
            self.data.update(state='done', message='任务已结束，已保留当前邮编部分 CSV。')
            self._checkpoint()

    def skip(self):
        with self.lock:
            if self.running() or self.data['state'] != 'paused':
                raise ValueError('请先等待任务暂停')
            self._export('用户跳过（未证明目录完成）')
            self._advance()
            self._checkpoint()
            if self.data['state'] != 'done':
                self.resume()

    def _wait(self):
        # Persist the deadline so a pause/restart cannot bypass the selected delay.
        with self.lock:
            deadline = self.data.get('next_run_at') or 0
        return not self.stop.wait(max(0, deadline - time.time()))

    def _delay(self):
        self.data['next_run_at'] = time.time() + self.data['options']['delay']

    def _page(self):
        d = self.data
        o = d['options']
        if o.get('provider')=='abn':
            from abn_bulk import query
            rows,meta=query(d['postcode'],o['state'],20,d['page'])
            if d['requests'] and d['requests'][0].get('snapshot')!=meta['snapshot']:
                raise ValueError('ABN 索引已更新，请结束当前任务并重新查询该邮编，避免分页遗漏')
            with self.lock:
                d['pending'].extend(rows);d['page']=meta['next_offset']
                d['requests'].append({'source':'ABN Bulk Extract','snapshot':meta['snapshot'],'offset':meta['offset']})
                if not meta['has_more']:
                    d['category_index']=len(o['categories']);d['reasons'].append({'reason':'本地官方快照邮编记录已遍历','category':'Active ABN','pages':d['page']})
                self._delay();self._checkpoint()
            return
        cat = o['categories'][d['category_index']]
        url = 'https://www.bing.com/maps/overlaybfpr?' + urlencode({
            'q': f'{CATEGORIES[cat]} near {o["state"]} {d["postcode"]} Australia',
            'count': 20, 'first': d['page'] * 20, 'setlang': 'en-US', 'cc': 'AU'})
        google=o.get('provider')=='google'
        if google:
            from google_places import search_page,ENDPOINT
            items,next_token=search_page(f'{CATEGORIES[cat]} near {o["state"]} {d["postcode"]} Australia',d.get('google_token',''))
            source,cached=ENDPOINT,False
        else:
            text, source, cached = fetch(url, o['refresh'], ttl=3600)
            items = parse_listings(text)
        with self.lock:
            d['requests'].append({'url': source, 'cached': cached})
            page_seen = set(d['category_seen'])
            seen = set(d['seen'])
            new_source = 0
            for item in items:
                key = json.dumps([re.sub(r'\W', '', value).casefold()
                                  for value in (item['title'], item['address'])])
                if key in page_seen:
                    continue
                page_seen.add(key)
                new_source += 1
                actual = re.search(r'\b([0-9]{4})\s*$', item['address'])
                target = address_matches(item['address'], d['postcode'], o['state'])
                state_match = re.search(r'\b(?:' + re.escape(o['state']) + '|' + re.escape(STATES[o['state']]) + r')\b', item['address'], re.I)
                if key in seen or not actual or not state_match or (not o['include_nearby'] and not target):
                    continue
                seen.add(key)
                ident = str(item.get('id', '')).removeprefix('ypid:')
                d['pending'].append({
                    'name': item['title'], 'category': item.get('primaryCategoryName') or cat,
                    'postcode': actual.group(1), 'requested_postcode': d['postcode'],
                    'location_scope': '目标邮编' if target else '邻区候选（距离未核实）',
                    'state': o['state'], 'address': item['address'], 'website': item.get('website') or '',
                    'phone': item.get('phone') or '', 'email': '', 'contact_page': '', 'observed': '',
                    'opportunity': '', 'status': '仅目录资料，待复核',
                    'source': item.get('source') or ('https://www.bing.com/maps?' + urlencode({'ss': 'ypid.' + ident}) if ident and not google else source),
                    'contact_source': ('Google Maps / Places' if google else 'Bing Maps')+' 商家目录（未电话验证）',
                    'checked_at': datetime.now(timezone.utc).isoformat(),
                    'notes': '目录可能过期；服务建议待商家确认。', 'abn': '', 'follow_up': '未联系'})
            d['seen'] = sorted(seen)
            d['category_seen'] = sorted(page_seen)
            d['page'] += 1
            if google:d['google_token']=next_token
            if new_source == 0 or d['page'] >= MAX_PAGES or (google and not next_token):
                d['reasons'].append({'category': cat, 'reason': 'Google 未返回后续分页令牌' if google and not next_token else ('无新增目录条目' if new_source == 0 else '达到安全分页上限（可能仍有遗漏）'), 'pages': d['page']})
                d['category_index'] += 1
                d['page'] = 0
                d['google_token'] = ''
                d['category_seen'] = []
            self._delay()
            self._checkpoint()

    def _export(self, reason):
        d = self.data
        name = f"{d['run_id']}_{d['options']['state']}_{d['postcode']}"
        metadata = {'postcode': d['postcode'], 'state': d['options']['state'],
                    'returned': len(d['rows']), 'reason': reason, 'category_end_reasons': d['reasons'],
                    'complete_area_census': False, 'options': d['options'], 'requests': d['requests'],
                    'saved_at': datetime.now(timezone.utc).isoformat()}
        atomic_write(self.folder / (name + '.csv'), csv_bytes(d['rows']))
        atomic_write(self.folder / (name + '.json'), json.dumps(metadata, ensure_ascii=False, indent=2).encode('utf-8'))
        entry = {'file': name + '.csv', 'postcode': d['postcode'], 'count': len(d['rows']), 'reason': reason}
        d['history'] = [h for h in d['history'] if h['file'] != entry['file']] + [entry]

    def _advance(self):
        d = self.data
        if int(d['postcode']) >= int(d['options']['end_postcode']):
            d.update(state='done', message='已到达结束邮编，CSV 已保存。', next_run_at=None)
        else:
            self._new_postcode(f"{int(d['postcode']) + 1:04d}")
            self._delay()

    def _run(self):
        try:
            while not self.stop.is_set() and self.data['state'] == 'running':
                if not self._wait():
                    break
                d = self.data
                complete = d['category_index'] >= len(d['options']['categories'])
                if len(d['pending']) >= 20 or (complete and d['pending']):
                    batch = copy.deepcopy(d['pending'][:20])
                    with self.lock:
                        d['message'] = f"邮编 {d['postcode']} · 第 {d['batches'] + 1} 批 · 核查 {len(batch)} 家商家"
                    if d.get('asic_batch'):
                        rows=d['asic_batch']
                    elif d['options'].get('provider')=='abn':
                        rows=batch
                    else:
                        with ThreadPoolExecutor(max_workers=4) as pool:
                            rows = list(pool.map(lambda row: enrich(row, d['options']['refresh']), batch))
                    if d['options'].get('use_asic'):
                        import browser_registry
                        previous=browser_registry.snapshot()
                        if d.get('asic_generation'):
                            if previous.get('generation')!=d['asic_generation']:raise ValueError('ASIC 队列已被替换；请停止或完成当前队列后重新启动邮编任务')
                            if previous['state']=='paused':raise ValueError('请先在插件继续 ASIC 队列，再继续邮编任务')
                        else:
                            if previous['state'] not in ('idle','done','stopped','error'):raise ValueError('ASIC 队列仍有任务；请先完成或停止后继续邮编任务')
                            browser_registry.start(rows,refresh=d['options']['refresh'])
                            d['asic_generation']=browser_registry.snapshot()['generation']
                            d['asic_batch']=rows
                            self._checkpoint()
                        while not self.stop.wait(1):
                            status=browser_registry.snapshot()
                            with self.lock:d['message']=f"邮编 {d['postcode']} · ASIC {status['completed']}/{status['total']} · {status['message']}"
                            if status['state']=='done':break
                            if status['state']=='paused' and status.get('pause_reason')=='verification':continue
                            if status['state'] in ('paused','error','stopped'):raise ValueError('ASIC 队列暂停或中断；已保存浏览器进度')
                        if self.stop.is_set():
                            browser_registry.pause('连续邮编任务已暂停')
                            break
                    with self.lock:
                        d.pop('asic_generation',None);d.pop('asic_batch',None)
                        d['rows'].extend(rows)
                        del d['pending'][:len(batch)]
                        d['batches'] += 1
                        self._export('进行中（部分结果）')
                        self._delay()
                        d['message'] = f"邮编 {d['postcode']} · 已保存 {len(d['rows'])} 家，等待 {d['options']['delay']} 秒"
                        self._checkpoint()
                elif complete:
                    with self.lock:
                        capped = sum('上限' in r['reason'] for r in d['reasons'])
                        reason = ('本地官方快照中该邮编的 Active ABN 已遍历' if d['options'].get('provider')=='abn' else (f'{capped} 个行业达到分页上限（可能遗漏）' if capped else '所选行业目录分页无新增条目'))
                        self._export(reason + '；非全量名册')
                        self._advance()
                        self._checkpoint()
                else:
                    with self.lock:
                        d['message'] = (f"邮编 {d['postcode']} · 官方快照已读取 {d['page']} 个 Active 主体" if d['options'].get('provider')=='abn' else f"邮编 {d['postcode']} · {d['options']['categories'][d['category_index']]} · 目录第 {d['page'] + 1}/{MAX_PAGES} 页")
                    self._page()
        except Exception as exc:
            with self.lock:
                self.data.update(error=str(exc), message='任务已暂停：' + str(exc) + '。可重试、跳过此邮编或结束任务。')
        finally:
            with self.lock:
                if self.data['state'] == 'running':
                    self.data['state'] = 'paused'
                    if not self.data.get('error'):
                        self.data['message'] = '已暂停并保存进度，可继续任务。'
                self._checkpoint()

    def download(self, name):
        with self.lock:
            if name not in {h['file'] for h in self.data['history']}:
                raise ValueError('文件不属于当前任务')
            return (self.folder / name).read_bytes()
