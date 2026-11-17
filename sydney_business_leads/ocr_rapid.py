"""Optional local CPU OCR worker. Models ship with the pinned runtime wheel."""
import json
import sys
from pathlib import Path

runtime=Path(__file__).resolve().parent.parent/'output'/'ocr_runtime'
if runtime.exists():sys.path.insert(0,str(runtime))
from rapidocr_onnxruntime import RapidOCR


def recognize(path):
    engine=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=2)
    result,timing=engine(path)
    if not result:raise ValueError('OCR 没有识别出文字')
    lines=[]
    for box,text,score in result:
        top=sum(p[1] for p in box)/4
        height=max(p[1] for p in box)-min(p[1] for p in box)
        lines.append({'text':text,'confidence':float(score),'y':top,'x':min(p[0] for p in box),'height':height})
    grouped=[]
    for line in sorted(lines,key=lambda v:v['y']):
        if grouped and abs(grouped[-1][0]['y']-line['y'])<max(6,line['height']*.45):grouped[-1].append(line)
        else:grouped.append([line])
    text='\n'.join(' '.join(v['text'] for v in sorted(group,key=lambda v:v['x'])) for group in grouped)
    return {'text':text.replace('：',':'),'engine':'RapidOCR ONNX local','lines':lines,'confidence':min(v['confidence'] for v in lines),'timing':timing}


if __name__=='__main__':
    print(json.dumps(recognize(sys.argv[1]),ensure_ascii=False))
