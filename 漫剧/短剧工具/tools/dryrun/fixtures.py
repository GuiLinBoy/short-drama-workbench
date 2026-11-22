"""Synthetic project inputs. Shared manifests are created by pipeline_state CLI."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import zlib

TOOLS=Path(__file__).resolve().parents[2]
SOURCE_ROOT=TOOLS.parents[1]
MARK='SIMULATED'


def write(path,text):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8',newline='\n') as f:f.write(text)


def json_file(path,obj):
    write(path,json.dumps(obj,ensure_ascii=False,indent=2)+'\n')


def hash_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def png(ratio,seed='mock'):
    rw,rh=map(int,ratio.split(':'))
    w,h=rw*40,rh*40
    tint=hashlib.sha256(seed.encode()).digest()
    raw=b''.join(b'\x00'+b''.join(bytes(((tint[0]+(x//40%2)*70)%256,
                    (tint[1]+(y//40%2)*70)%256,tint[2])) for x in range(w)) for y in range(h))
    def chunk(tag,data):return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')


def create(root):
    manju=root/'漫剧'
    shadow=manju/'短剧工具'
    for rel in ['genres/3d-guoman.md','genres/live-action.md','genres/urban.md','镜头语言手册.md',
                'skills/nexorai-image/SKILL.md','skills/minimax-h3-video/references/pitfalls.md']:
        target=shadow/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(TOOLS/rel,target)
    json_file(manju/'全局设置.json',{'h3':{'max_running_tasks':1},
              'nexorai':{'max_running':1,'images_cap_per_day':50,'images_per_batch':12},'active_projects_max':3})
    projects=[]
    for letter,ratio,durations in [('A','9:16',[8,8,8]),('B','16:9',[4,4])]:
        p=manju/'作品'/f'演练{letter}'
        project={'schema_version':2,'title':f'SIMULATED 演练{letter}','mode':'short','genre':'3d-guoman',
                 'ratio':ratio,'character_sheet_ratio':'16:9','source':{'path':'原文/模拟.txt'},
                 'image':{'provider':'nexorai','model':'grok-imagine-image-quality'},
                 'video':{'provider':'minimax-h3','model':'MiniMax-H3','draft_resolution':'480P',
                          'final_resolution':'768P','audio':'generate','use_context_ir':True},
                 'narration':{'provider':'compshare-indextts-2.5','voice_id':'模拟音色','speed':1},
                 'post':{'subtitles':'srt','burn_subtitles':True,'loudness_lufs':-14,'true_peak_db':-1,
                         'bgm_duck_db':12,'aigc_label':{'visible':True,'metadata':True,
                         'text':'离线演练 含 AI 模拟内容','badge':'SIMULATED','badge_position':'top-right'}},
                 'publish':False,'dryrun':MARK}
        json_file(p/'project.json',project)
        write(p/'原文/模拟.txt',f'{MARK} PROJECT={letter}：仅测试工具衔接，不是故事作品。\n')
        json_file(p/'approvals/budget.json',[{'batch':f'EP001-{label}','kind':kind,'unit':unit,'limit':limit,'used':0,
                  'approval':MARK} for label,kind,unit,limit in [('参考图','image','images',12),
                  ('旁白','speech','points',50),('试生成','video','points',500),('定稿','video','points',800)]])
        json_file(p/'approvals/tts-catalog.json',{'voices':[{'name':'模拟音色','voice_id':'dryrun-voice'}],
                                                   'unit_price':.01,'source':MARK})
        json_file(p/'approvals/simulated.json',{'approval':MARK,'real_authorization':False})
        assets=[]
        for aid,kind,name in [('C00001-A','character','模拟角色'),('S00001','scene','模拟场景')]:
            detail=f'资产/{aid}.txt'
            prompt=f'资产/{aid}.prompt.txt'
            write(p/detail,f'{MARK} {name} PROJECT={letter}\n')
            write(p/prompt,f'{MARK} PROJECT={letter} identity={aid}; checkerboard test fixture.\n')
            a={'id':aid,'type':kind,'name':name,'detail':detail,'prompt':prompt,
               'image':f'资产/{aid}.png','status':'planned'}
            if kind=='character':
                a.update(sheet=f'资产/{aid}.sheet.png',sheet_prompt=f'资产/{aid}.sheet.prompt.txt',
                         voice='A synthetic test tone, not a real voice.')
                write(p/a['sheet_prompt'],f'{MARK} PROJECT={letter}; use 图1 as identity reference, three views.\n')
            assets.append(a)
        clips=[]
        for i,duration in enumerate(durations,1):
            clip=f'Clip{i:05d}'
            c={'id':f'EP001/{clip}','episode':'EP001','duration':duration,'assets':['C00001-A','S00001'],
               'prompt':f'单集制作/EP001/{clip}.prompt.txt','video':f'单集制作/EP001/{clip}.mp4',
               'continue_from':f'EP001/Clip{i-1:05d}' if i>1 else None,'status':'planned',
               'needs_draft':True,'shots':[{'start':0,'end':duration,'visual':'SIMULATED fixture','dialogue':'离线测试台词。'}],
               'draft':{'video':f'单集制作/EP001/试生成/{clip}.mp4','status':'planned'},
               'narration':[{'id':f'{clip}-1','start':duration-1.5,'reserved':1.5,'text':'这是模拟旁白。'}]}
            if i==1:c['voice_anchor']=['C00001']
            clips.append(c)
            write(p/c['prompt'],prompt_text(letter,i,False,False))
        projects.append({'letter':letter,'root':p,'project':project,'assets':assets,'clips':clips})
    json_file(manju/'作品清单.json',{'mode':MARK,'fixture_only':True,
              'projects':[{'name':p['root'].name,'root':str(p['root']),'status':'SIMULATED_ACTIVE'} for p in projects]})
    return manju,projects


def prompt_text(letter,index,tail,voice):
    text=(f'SIMULATED PROJECT={letter} CLIP={index}; 3D test pattern, no real generation.\n'
          '[C00001-A] is Subject 1; [S00001] is the background.\n'
          'Subject 1 says: <d>离线测试台词。</d> Then everyone remains silent.\n'
          'non_diegetic_music: N/A\n')
    if tail:
        text+=f'[Clip{index-1:05d}-尾帧] is the continuity reference.\n{{{{承接定义：SIMULATED placeholder}}}}\n{{{{承接开场：SIMULATED placeholder}}}}\n'
    if voice:text+='[音色参考] is the synthetic voice reference.\n'
    return text
