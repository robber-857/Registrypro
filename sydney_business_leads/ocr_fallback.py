"""Local screenshot OCR, same server-side identity/date validation as DOM input."""
import base64
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

def structure_local(out):
    text=out.get('text','')
    query=re.search(r'results? found for\s+["“]([^"”]+)',text,re.I)
    out['page_type']='results' if query else 'business_summary' if 'Business Name Summary' in text else 'unknown'
    out['query']=query.group(1) if query else ''
    out['rows']=[]
    if query:
        for line in text.splitlines():
            match=re.match(r'(.+?)\s+(Not Available|[\d ]{9,15})\s+(Cancelled|Registered|Deregistered)\b',line,re.I)
            if match:
                digits=re.sub(r'\D','',match[2])
                out['rows'].append(dict(name=match[1],status=match[3],abn=digits if len(digits)==11 else '',acn='',renewal_date=''))
    return out


def extract(data_url, folder, identifier):
    if not isinstance(data_url,str) or not data_url.startswith('data:image/png;base64,'):
        raise ValueError('OCR 需要 PNG 截图')
    try:
        data=base64.b64decode(data_url.split(',',1)[1],validate=True)
    except ValueError:
        raise ValueError('截图编码无效') from None
    if len(data)>4_000_000 or len(data)<24 or data[:8]!=b'\x89PNG\r\n\x1a\n':
        raise ValueError('PNG 截图格式或大小不正确')
    width,height=struct.unpack('>II',data[16:24])
    if not 1<=width<=4096 or not 1<=height<=4096:
        raise ValueError('OCR 截图尺寸必须在 4096 像素以内')
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    path=folder/(identifier+'.png');path.write_bytes(data)
    from ocr_vision import extract as vision_extract
    vision=vision_extract(data_url)
    if vision is not None:
        vision['screenshot']=str(path)
        return vision
    rapid=Path(__file__).with_name('ocr_rapid.py')
    try:
        result=subprocess.run([sys.executable,str(rapid),str(path.resolve())],capture_output=True,timeout=35,env={**os.environ,'PYTHONUTF8':'1'},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode==0:
            out=json.loads(result.stdout.decode('utf-8-sig'))
            if out['confidence']<0.8:raise ValueError('OCR 置信度不足，保留截图但不采纳字段')
            out['screenshot']=str(path)
            return structure_local(out)
    except subprocess.TimeoutExpired:
        raise ValueError('本机 OCR 超时，未猜测字段') from None
    command=['powershell.exe','-NoProfile','-Command','-']
    script=Path(__file__).with_name('ocr_windows.ps1').read_text(encoding='utf-8').replace('param([Parameter(Mandatory=$true)][string]$ImagePath)', '$ImagePath = $env:REGISTRY_OCR_IMAGE_PATH')+'\n\n'
    environment={**os.environ,'REGISTRY_OCR_IMAGE_PATH':str(path.resolve())}
    try:
        result=subprocess.run(command,input=script.encode(),env=environment,capture_output=True,timeout=35,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise ValueError('本机 OCR 无法运行：'+type(exc).__name__) from None
    if result.returncode:
        raise ValueError('Windows OCR 不可用：'+result.stderr.decode('utf-8',errors='replace')[-400:])
    out=json.loads(result.stdout.decode('utf-8-sig'))
    out['screenshot']=str(path)
    return structure_local(out)
