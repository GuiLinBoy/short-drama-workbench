#!/usr/bin/env python3
"""Prepare an argv request and numbered image mapping; does not submit."""
import argparse,json,os,tempfile,hashlib,re
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',required=True,choices=['text2image','image2image','multimodal2video','text2video'])
    p.add_argument('--prompt',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--reference',action='append',default=[],metavar='LABEL=PATH')
    p.add_argument('--model');p.add_argument('--ratio',default='16:9');p.add_argument('--duration',type=int)
    p.add_argument('--resolution');a=p.parse_args()
    if a.output.exists():p.error('Output exists; choose a new request filename')
    body=a.prompt.read_text(encoding='utf-8').strip()
    if not body:p.error('Prompt is empty')
    refs=[];labels=set()
    for item in a.reference:
        label,sep,name=item.partition('=')
        if not sep or not label or label in labels:p.error('Each reference needs unique LABEL=PATH')
        path=Path(name).resolve()
        if not path.is_file() or not path.stat().st_size:p.error('Missing/empty reference: '+name)
        labels.add(label);refs.append((label,path))
    video=a.mode.endswith('video')
    limit=9 if a.mode=='multimodal2video' else 10
    if len(refs)>limit:p.error('Image count exceeds verified CLI limit')
    if a.mode in ('image2image','multimodal2video') and not refs:p.error('This mode requires references')
    if a.mode in ('text2image','text2video') and refs:p.error('Text-only mode cannot attach images')
    if video and (a.duration is None or not 4<=a.duration<=15):p.error('Verified Seedance profile requires integer duration 4..15; update profile for other models')
    if not video and a.duration is not None:p.error('Image mode does not take duration')
    referenced=set(re.findall(r'\[(?:[CSP]\d{5}(?:-[A-Z]+)?|Clip\d+-尾帧)\]',body))
    if refs and not referenced.issubset(labels):p.error('Missing mapped assets: '+','.join(sorted(referenced-labels)))
    if refs and re.search(r'(?:图|图片)\s*\d+',body):p.error('Body contains fixed image numbers; use reference labels and let the script assign numbers')
    prefix='；'.join(f'{label}对应图{i}' for i,(label,_) in enumerate(refs,1))
    prompt=(prefix+'。\n\n' if prefix else '')+body
    argv=['dreamina',a.mode,'--prompt',prompt,'--ratio',a.ratio,'--poll','0']
    if a.model:argv+=['--model_version',a.model]
    if a.resolution:argv+=['--video_resolution' if video else '--resolution_type',a.resolution]
    if video:argv+=['--duration',str(a.duration)]
    for _,path in refs:argv+=['--images' if a.mode=='image2image' else '--image',str(path)]
    # Image2image's strings flag accepts repeated instances; reject comma ambiguity.
    if a.mode=='image2image' and any(',' in str(path) for _,path in refs):p.error('Comma in image path is ambiguous for --images; use a copy without commas')
    record={'schema_version':1,'argv':argv,'references':[{'label':label,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for label,path in refs]}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.with_suffix('.submit.prompt.txt').write_text(prompt+'\n',encoding='utf-8')
    a.output.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(a.output)
if __name__=='__main__':main()
