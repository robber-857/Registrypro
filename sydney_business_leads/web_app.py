"""Loopback-only research UI. No credentials, external writes or outbound mail."""
import argparse
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from local_leads import HERE, OUTPUT, CATEGORIES, STATES, collect, csv_bytes, annotated_csv, save_run, validate, CollectionError
import registration
import browser_registry
from postcode_automation import Automation

TOKEN=secrets.token_urlsafe(24)
LOCK=threading.Lock()
JOB={'state':'idle','message':'准备就绪','rows':[],'metadata':{},'file':''}
for previous in sorted(OUTPUT.glob('leads_*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:1]:
    try:
        saved=json.loads(previous.read_text(encoding='utf-8'))
        JOB.update(state='done',message='已恢复最近一次查询',rows=saved['rows'],metadata=saved['metadata'],file=previous.stem)
    except (OSError,ValueError,KeyError):pass
CANCEL=threading.Event()
AUTO=Automation()
AUDIT={'state':'idle','message':'新查询会自动核对注册资料；已有名单可点击一键核对。','rows':[],'scope':''}
AUDIT_CANCEL=threading.Event()


def audit_worker(rows,scope):
    from auto_registration import investigate
    done=[]
    try:
        for index,row in enumerate(rows):
            if AUDIT_CANCEL.is_set():break
            with LOCK:AUDIT.update(message=f'自动核对 {index+1}/{len(rows)} · {row["name"]}')
            done.append(investigate(row,refresh=True))
            with LOCK:AUDIT.update(rows=list(done))
            if index+1<len(rows) and AUDIT_CANCEL.wait(1):break
        with LOCK:AUDIT.update(state='done',message=f'自动核对已{"停止" if AUDIT_CANCEL.is_set() else "完成"}：{len(done)} 家，规则匹配 {sum(r.get("identity_status")=="自动匹配" for r in done)} 家。结果和原因已保存。')
    except Exception as exc:
        with LOCK:AUDIT.update(state='error',message='自动核对中断：'+str(exc))


def reviewed():
    rows=json.loads((HERE/'burwood_reviewed.json').read_text(encoding='utf-8'))
    for row in rows:
        row.update(requested_postcode='2134',location_scope='目标邮编',contact_source=row['source'],contact_page=row['source'])
    return registration.apply(rows)


def worker(options):
    def progress(message, rows):
        with LOCK:
            JOB.update(message=message,rows=list(rows))
    try:
        rows,metadata=collect(**options,progress=progress,cancelled=CANCEL.is_set)
        file=save_run(rows,metadata)
        with LOCK:
            JOB.update(state='done',message=f'已整理 {len(rows)} 家商家',rows=rows,metadata=metadata,file=file)
    except Exception as e:
        # Retain any rows already delivered by progress on an unexpected error.
        with LOCK:
            rows=list(JOB['rows'])
        metadata={'postcode':options['postcode'],'state':options['state'],'status':'failed','error':str(e)}
        file=save_run(rows,metadata) if rows else ''
        with LOCK:
            JOB.update(state='error',message=str(e),metadata=metadata,file=file)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass
    def reply(self,status,body,content_type='application/json; charset=utf-8',filename=None):
        if isinstance(body,(dict,list)):
            body=json.dumps(body,ensure_ascii=False).encode('utf-8')
        elif isinstance(body,str):
            body=body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        if filename:
            self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
        self.end_headers(); self.wfile.write(body)
    def valid_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
    def do_GET(self):
        if not self.valid_host():
            return self.reply(403,{'error':'Invalid host'})
        path=urlsplit(self.path).path
        if path=='/':
            return self.reply(200,(HERE/'web/index.html').read_text(encoding='utf-8').replace('__TOKEN__',TOKEN),'text/html; charset=utf-8')
        if path=='/api/reviewed':
            return self.reply(200,{'rows':reviewed(),'metadata':{'status':'reviewed','postcode':'2134','date':'2026-11-17'}})
        if path=='/api/status':
            with LOCK:
                return self.reply(200,{**JOB,'rows':registration.apply(JOB['rows'])})
        if path=='/api/automation':
            snapshot=AUTO.snapshot()
            snapshot['rows']=registration.apply(snapshot['rows'])
            return self.reply(200,snapshot)
        if path=='/api/registration/status':
            with LOCK:return self.reply(200,AUDIT)
        if path=='/api/browser/status':return self.reply(200,browser_registry.snapshot())
        if path=='/api/browser/next':
            if self.headers.get('X-Local-Token')!=TOKEN:return self.reply(403,{'error':'配对码已失效，请从本机页面重新复制'})
            return self.reply(200,{'task':browser_registry.claim(),'status':browser_registry.snapshot()})
        if path.startswith('/download/automation/'):
            name=path.removeprefix('/download/automation/')
            try:
                return self.reply(200,annotated_csv(AUTO.download(name)),'text/csv; charset=utf-8',name)
            except (ValueError,OSError):
                return self.reply(404,{'error':'没有此任务文件'})
        if path=='/download/reviewed.csv':
            return self.reply(200,csv_bytes(reviewed()),'text/csv; charset=utf-8','Burwood_20_businesses.csv')
        if path=='/download/reviewed.xlsx':
            file=HERE.parent/'outputs/burwood/Burwood_20_businesses.xlsx'
            if not file.exists(): return self.reply(404,{'error':'Excel 文件尚未生成'})
            return self.reply(200,file.read_bytes(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','Burwood_20_businesses.xlsx')
        if path=='/download/latest.csv':
            with LOCK:
                name=JOB['file']
            if not name: return self.reply(404,{'error':'还没有完成的导出'})
            return self.reply(200,annotated_csv((OUTPUT/(name+'.csv')).read_bytes()),'text/csv; charset=utf-8',name+'.csv')
        self.reply(404,{'error':'Not found'})
    def do_POST(self):
        if not self.valid_host() or self.headers.get('X-Local-Token')!=TOKEN:
            return self.reply(403,{'error':'请从本机页面操作。'})
        if self.path.startswith('/api/browser/'):
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 1<=length<=180000:raise ValueError('请求大小不正确')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValueError('请求格式不正确')
                if self.path=='/api/browser/result':return self.reply(200,{'message':browser_registry.result(body)})
                if self.path=='/api/browser/stop':
                    browser_registry.stop();return self.reply(200,{'ok':True})
                if self.path=='/api/browser/start':
                    with LOCK:
                        if JOB['state']=='running' or AUTO.running() or AUDIT['state']=='running':raise ValueError('请先等待或暂停当前采集任务')
                        scope=body.get('scope')
                        if scope=='manual':rows=registration.apply(JOB['rows'])
                        elif scope=='reviewed':rows=reviewed()
                        elif scope=='automation':rows=registration.apply(AUTO.snapshot()['rows'])
                        else:raise ValueError('名单范围不正确')
                        if not rows:raise ValueError('名单为空')
                        browser_registry.start(rows)
                    return self.reply(202,{'ok':True})
                return self.reply(404,{'error':'Not found'})
            except (ValueError,TypeError,OSError) as exc:return self.reply(400,{'error':str(exc)})
        if self.path=='/api/registration/stop':
            AUDIT_CANCEL.set();return self.reply(200,{'ok':True})
        if self.path=='/api/registration/auto':
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 1<=length<=4096:raise ValueError('请求大小不正确')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValueError('请求格式不正确')
                scope=body.get('scope')
                with LOCK:
                    if JOB['state']=='running' or AUTO.running() or AUDIT['state']=='running':raise ValueError('请等待当前任务结束或先暂停')
                    if scope=='reviewed':rows=reviewed()
                    elif scope=='manual':rows=registration.apply(JOB['rows'])
                    elif scope=='automation':rows=registration.apply(AUTO.snapshot()['rows'])
                    else:raise ValueError('请选择有效名单')
                    if body.get('key'):rows=[r for r in rows if r['registration_key']==body['key']]
                    if not rows:raise ValueError('名单没有可核对的商家')
                    AUDIT_CANCEL.clear();AUDIT.update(state='running',scope=scope,rows=[],message='开始自动查找注册资料')
                threading.Thread(target=audit_worker,args=(rows,scope),daemon=True).start()
                return self.reply(202,{'ok':True})
            except (ValueError,TypeError) as exc:return self.reply(400,{'error':str(exc)})
        if self.path=='/api/registration':
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 1<=length<=24000: raise ValueError('请求大小不正确')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict): raise ValueError('请求格式不正确')
                data=registration.save(body.get('key'),body.get('values'))
                return self.reply(200,{'registration':data})
            except (ValueError,TypeError) as e:
                return self.reply(400,{'error':str(e)})
        if self.path.startswith('/api/automation/'):
            try:
                action=self.path.rsplit('/',1)[-1]
                with LOCK:
                    if JOB['state']=='running' or AUDIT['state']=='running': raise ValueError('请先等待当前查询结束')
                    if action=='start':
                        length=int(self.headers.get('Content-Length','0'))
                        if not 1<=length<=4096: raise ValueError('请求大小不正确')
                        AUTO.start(json.loads(self.rfile.read(length)))
                    elif action=='pause': AUTO.pause()
                    elif action=='resume': AUTO.resume()
                    elif action=='finish': AUTO.finish()
                    elif action=='skip': AUTO.skip()
                    else: return self.reply(404,{'error':'Not found'})
                return self.reply(202,{'ok':True})
            except (ValueError,TypeError,CollectionError) as e:
                return self.reply(400,{'error':str(e)})
        if self.path=='/api/cancel':
            CANCEL.set(); return self.reply(200,{'ok':True})
        if self.path!='/api/collect': return self.reply(404,{'error':'Not found'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>4096: raise ValueError('请求大小不正确')
            options=json.loads(self.rfile.read(length))
            if not isinstance(options,dict): raise ValueError('请求格式不正确')
            allowed={'postcode','state','limit','categories','refresh','include_nearby'}
            if set(options)-allowed: raise ValueError('不支持的查询参数')
            validate(options.get('postcode'),options.get('state'),options.get('limit'))
            if not options.get('categories') or any(c not in CATEGORIES for c in options['categories']):
                raise ValueError('至少选择一个行业')
            for flag in ('refresh','include_nearby'):
                if flag in options and not isinstance(options[flag],bool): raise ValueError('选项格式不正确')
        except (ValueError,TypeError,CollectionError) as e:
            return self.reply(400,{'error':str(e)})
        with LOCK:
            if JOB['state']=='running' or AUTO.running() or AUDIT['state']=='running': return self.reply(409,{'error':'已有查询正在进行'})
            CANCEL.clear(); JOB.update(state='running',message='开始搜索',rows=[],metadata={},file='')
        threading.Thread(target=worker,args=(options,),daemon=True).start()
        self.reply(202,{'ok':True})


def main():
    p=argparse.ArgumentParser(); p.add_argument('--port',type=int,default=8765); p.add_argument('--no-browser',action='store_true')
    a=p.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler)
    url=f'http://127.0.0.1:{a.port}'
    print('本地商家研究工具：'+url,flush=True)
    if not a.no_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__=='__main__': main()
