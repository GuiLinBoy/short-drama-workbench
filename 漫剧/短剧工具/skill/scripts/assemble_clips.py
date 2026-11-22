#!/usr/bin/env python3
"""Normalize approved clips and concatenate with audio preservation and verification."""
import argparse,json,os,subprocess,tempfile
from pathlib import Path

def run(argv):return subprocess.run(argv,check=True,capture_output=True,text=True)
def probe(path):return json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]).stdout)
def contained(root,name):
    p=Path(name)
    if p.is_absolute() or not (root/p).resolve().is_relative_to(root):raise ValueError('Path escapes project: '+name)
    return (root/p).resolve()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project-root',type=Path,required=True)
    p.add_argument('--manifest',default='clips.json');p.add_argument('--output',default='成片.mp4')
    p.add_argument('--episode');p.add_argument('--width',type=int,default=1280);p.add_argument('--height',type=int,default=720)
    p.add_argument('--fps',type=int,default=30);p.add_argument('--overwrite',action='store_true');a=p.parse_args()
    if a.width<2 or a.height<2 or a.width%2 or a.height%2 or a.fps<1:p.error('Even positive dimensions and positive fps required')
    root=a.project_root.resolve();out=contained(root,a.output)
    if out.suffix.lower()!='.mp4':p.error('Output must be .mp4')
    if out.exists() and not a.overwrite:p.error('Output exists; use --overwrite for intentional replacement')
    clips=json.loads(contained(root,a.manifest).read_text(encoding='utf-8'))
    if a.episode:clips=[c for c in clips if c['episode']==a.episode]
    if not clips:p.error('No clips selected')
    if len({c['id'] for c in clips})!=len(clips):p.error('Duplicate clip IDs')
    if any(c.get('status')!='approved' for c in clips):p.error('All selected clips require approved status')
    sources=[contained(root,c['video']) for c in clips]
    if out in sources:p.error('Output must not overwrite source video')
    out.parent.mkdir(parents=True,exist_ok=True);total=0
    with tempfile.TemporaryDirectory(prefix='assemble-',dir=out.parent) as folder:
        temp=Path(folder);normalized=[]
        for i,(clip,src) in enumerate(zip(clips,sources)):
            info=probe(src);videos=[s for s in info['streams'] if s['codec_type']=='video']
            if not videos:raise ValueError('No video: '+str(src))
            duration=float(videos[0].get('duration') or info['format']['duration'])
            if duration<=0:raise ValueError('Invalid duration')
            if abs(duration-float(clip['duration']))>max(0.25,2/a.fps):raise ValueError('Clip differs from planned duration: '+clip['id'])
            total+=duration;audio=any(s['codec_type']=='audio' for s in info['streams'])
            dst=temp/f'{i:06d}.mp4';argv=['ffmpeg','-v','error','-y','-i',str(src)]
            if not audio:argv+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
            vf=f'setpts=PTS-STARTPTS,scale={a.width}:{a.height}:force_original_aspect_ratio=decrease,pad={a.width}:{a.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={a.fps},format=yuv420p'
            argv+=['-map','0:v:0','-map','0:a:0' if audio else '1:a:0','-vf',vf,
                   '-af','asetpts=PTS-STARTPTS,aresample=48000:async=1:first_pts=0,apad',
                   '-t',str(duration),'-c:v','libx264','-preset','fast','-crf','18','-c:a','aac','-ar','48000','-ac','2',str(dst)]
            run(argv);normalized.append(dst)
        listing=temp/'list.txt';listing.write_text(''.join(f"file '{f.name}'\n" for f in normalized),encoding='utf-8')
        final=temp/'final.mp4'
        run(['ffmpeg','-v','error','-y','-f','concat','-safe','1','-i',str(listing),'-c','copy','-movflags','+faststart',str(final)])
        run(['ffmpeg','-v','error','-xerror','-i',str(final),'-f','null','-'])
        info=probe(final);actual=float(info['format']['duration']);tolerance=max(0.25,len(clips)*2/a.fps)
        if abs(actual-total)>tolerance:raise ValueError(f'Final duration mismatch: expected {total}, actual {actual}')
        if not any(s['codec_type']=='audio' for s in info['streams']):raise ValueError('Final audio missing')
        os.replace(final,out)
        report={'clips':[c['id'] for c in clips],'expected_duration':total,'actual_duration':actual,'tolerance':tolerance,'width':a.width,'height':a.height,'fps':a.fps,'full_decode':'passed','visual_review':'required'}
        out.with_suffix('.validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(out)
if __name__=='__main__':main()
