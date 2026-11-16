"""Loopback-only research UI. No credentials, external writes or outbound mail."""
import argparse
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from local_leads import HERE, OUTPUT, CATEGORIES, STATES, collect, csv_bytes, save_run, validate, CollectionError

TOKEN=secrets.token_urlsafe(24)
LOCK=threading.Lock()
JOB={'state':'idle','message':'准备就绪','rows':[],'metadata':{},'file':''}
CANCEL=threading.Event()


def reviewed():
    rows=json.loads((HERE/'burwood_reviewed.json').read_text(encoding='utf-8'))
    for row in rows:
        row.update(requested_postcode='2134',location_scope='目标邮编',contact_source=row['source'],contact_page=row['source'])
    return rows


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
                return self.reply(200,JOB)
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
            return self.reply(200,(OUTPUT/(name+'.csv')).read_bytes(),'text/csv; charset=utf-8',name+'.csv')
        self.reply(404,{'error':'Not found'})
    def do_POST(self):
        if not self.valid_host() or self.headers.get('X-Local-Token')!=TOKEN:
            return self.reply(403,{'error':'请从本机页面操作。'})
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
            if JOB['state']=='running': return self.reply(409,{'error':'已有查询正在进行'})
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
