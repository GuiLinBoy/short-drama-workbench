#!/usr/bin/env python3
"""Validate project references and stage-specific files, not visual quality."""
import argparse,json,re,sys
from pathlib import Path

def validate(root,stage):
    errors=[]
    def path(value):
        if not isinstance(value,str) or not value: raise ValueError('Empty/non-string path')
        p=Path(value)
        if p.is_absolute() or not (root/p).resolve().is_relative_to(root):raise ValueError('Path escapes project: '+value)
        return root/p
    def file(value,label):
        try:
            p=path(value)
            if not p.is_file() or not p.stat().st_size:errors.append(label+': missing/empty '+value)
        except ValueError as e:errors.append(str(e))
    assets=json.loads((root/'assets.json').read_text(encoding='utf-8'))
    clips=json.loads((root/'clips.json').read_text(encoding='utf-8'))
    if not isinstance(assets,list) or not isinstance(clips,list):raise ValueError('assets/clips must be arrays')
    seen={}
    for a in assets:
        for k in ('id','type','name','detail','prompt','image','status'):
            if k not in a:errors.append('Asset missing '+k)
        aid=a.get('id','')
        kind=a.get('type');pat={'character':r'C\d{5}-[A-Z]+','scene':r'S\d{5}','prop':r'P\d{5}'}.get(kind,'(?!)')
        if not re.fullmatch(pat,aid):errors.append('Invalid asset ID/type: '+aid)
        if aid in seen:errors.append('Duplicate asset '+aid)
        seen[aid]=a
        if a.get('status') not in ('planned','generated','approved','failed'):errors.append('Invalid asset status '+aid)
        for k in ('detail','prompt','image'):
            try:path(a.get(k))
            except ValueError as e:errors.append(str(e))
        file(a.get('detail',''),aid)
        if stage in ('assets','assemble'):
            file(a.get('prompt',''),aid);file(a.get('image',''),aid)
            if a.get('status')!='approved':errors.append('Asset not approved '+aid)
    for aid in seen:
        visited=set();cur=aid
        while cur:
            if cur in visited:errors.append('Asset source cycle '+aid);break
            visited.add(cur)
            if cur not in seen:errors.append('Unknown source asset '+cur);break
            cur=seen[cur].get('source_asset_id')
    prior=set()
    for c in clips:
        for k in ('id','episode','duration','assets','prompt','video','continue_from','status'):
            if k not in c:errors.append('Clip missing '+k)
        cid=c.get('id','')
        if cid in prior:errors.append('Duplicate clip '+cid)
        if not re.fullmatch(r'EP\d{3,}/Clip\d{5,}',cid):errors.append('Invalid clip ID '+cid)
        if not cid.startswith(str(c.get('episode'))+'/'):errors.append('Episode mismatch '+cid)
        if c.get('continue_from') and c['continue_from'] not in prior:errors.append('Continuity must reference earlier clip '+cid)
        prior.add(cid)
        d=c.get('duration')
        if isinstance(d,bool) or not isinstance(d,(int,float)) or d<=0:errors.append('Invalid duration '+cid)
        for aid in c.get('assets',[]):
            if aid not in seen:errors.append('Unknown asset '+str(aid)+' in '+cid)
        if c.get('status') not in ('planned','generated','approved','failed'):errors.append('Invalid clip status '+cid)
        for k in ('prompt','video'):
            try:path(c.get(k))
            except ValueError as e:errors.append(str(e))
        if stage in ('assets','assemble'):file(c.get('prompt',''),cid)
        if stage=='assemble':
            file(c.get('video',''),cid)
            if c.get('status')!='approved':errors.append('Clip not approved '+cid)
        if c.get('shots'):
            end=0
            for shot in c['shots']:
                start,stop=shot.get('start'),shot.get('end')
                if not isinstance(start,(int,float)) or not isinstance(stop,(int,float)) or abs(start-end)>0.001 or stop<=start:
                    errors.append('Invalid shot timeline '+cid);break
                end=stop
            if isinstance(d,(int,float)) and abs(end-d)>0.001:errors.append('Shot duration mismatch '+cid)
    return errors

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project-root',type=Path,required=True)
    p.add_argument('--stage',choices=['plan','assets','assemble'],default='plan');a=p.parse_args()
    try:errors=validate(a.project_root.resolve(),a.stage)
    except (ValueError,KeyError,TypeError,OSError) as e:errors=[str(e)]
    print(json.dumps({'ok':not errors,'stage':a.stage,'errors':errors},ensure_ascii=False,indent=2))
    sys.exit(bool(errors))
if __name__=='__main__':main()
