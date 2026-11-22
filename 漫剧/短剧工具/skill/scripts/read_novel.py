#!/usr/bin/env python3
"""Read an explicit character range without executing source contents."""
import argparse, hashlib, json
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('file',type=Path);p.add_argument('--info',action='store_true')
    p.add_argument('--start',type=int,default=0);p.add_argument('--length',type=int,default=3000)
    p.add_argument('--end',type=int);p.add_argument('--encoding',default='utf-8-sig')
    a=p.parse_args()
    raw=a.file.read_bytes();s=raw.decode(a.encoding)
    end=len(s) if a.end is None else a.end
    if not 0 <= a.start <= end <= len(s) or a.length < 1:
        p.error('Require 0 <= start <= end <= total and length > 0')
    result={'source_sha256':hashlib.sha256(raw).hexdigest(),'encoding':a.encoding,'total_characters':len(s)}
    if a.info: result['lines']=len(s.splitlines())
    else:
        stop=min(a.start+a.length,end)
        result.update(start=a.start,end=stop,next_start=stop,range_end=end,done=stop==end,content=s[a.start:stop])
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__': main()
