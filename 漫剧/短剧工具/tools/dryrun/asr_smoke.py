"""Independent real local ASR gate; never substitute a recognition fixture."""
import json
import math
from pathlib import Path
import time
import unicodedata
import wave

from fixtures import SOURCE_ROOT,hash_file,write,json_file

EXPECTED='这扇门，我一定要推开。哼，你以为我会怕你？'


def norm(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if c.isalnum())


def run_checkpoint(r,environment_root=None):
    base=Path(environment_root or SOURCE_ROOT/'环境')
    model_root=base/'models/models'
    model=model_root/'iic--speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/snapshots/master'
    vad=model_root/'iic--speech_fsmn_vad_zh-cn-16k-common-pytorch/snapshots/master'
    python=base/'asr/Scripts/python.exe'
    audio=base/'测试音频/主会话-台词.wav'
    paths=[python,audio,model,vad]
    result={'status':'NOT_RUN','test_stub':False,'real_local_inference':True,
            'expected':EXPECTED,'source':str(audio),'models':[str(model),str(vad)]}
    missing=[str(p) for p in paths if not p.exists()]
    if missing:
        return {**result,'status':'BLOCKED','reason':'Missing existing local prerequisites; no downloads allowed','missing':missing}
    if r.env.get('ASR_FAKE_RESULT'):
        return {**result,'status':'FAIL','reason':'Recognition stub must not be set for this gate'}
    folder=r.root/'local-asr'
    folder.mkdir()
    before=hash_file(audio)
    started=time.monotonic()
    try:
        env={'ASR_PY':str(python),'ASR_MODELS':str(base/'models'),
             'ASR_LOCAL_MODEL':str(model),'ASR_LOCAL_VAD':str(vad),
             'HF_HUB_OFFLINE':'1','MODELSCOPE_OFFLINE':'1'}
        aligned=folder/'alignment.json'
        r.cli('align','align',audio,'--out',aligned,env=env,label='REAL LOCAL ASR: recognize and timestamp')
        data=json.loads(aligned.read_text(encoding='utf-8'))
        result.update(recognized=data['text'],alignment=str(aligned),source_sha256=before)
        if norm(data['text'])!=norm(EXPECTED):
            raise RuntimeError('Actual recognition differs from expected text; no stub fallback')
        with wave.open(str(audio)) as wav:duration=wav.getnframes()/wav.getframerate()
        stamps=data.get('timestamp',[])
        if len(stamps)!=len(norm(EXPECTED)) or not stamps:
            raise RuntimeError('Actual character timestamp count differs')
        for i,(a,b) in enumerate(stamps):
            if not all(math.isfinite(x) for x in (a,b)) or not 0<=a<b<=duration*1000+100:
                raise RuntimeError('Invalid actual ASR timestamp')
            if i and (a<stamps[i-1][0] or b<stamps[i-1][1]):
                raise RuntimeError('Actual ASR timestamps are out of order')
        segments=data.get('speech_segments',[])
        if not segments or any(not 0<=a<b<=duration+.1 for a,b in segments):
            raise RuntimeError('Invalid actual VAD segments')
        script=folder/'script.txt';write(script,EXPECTED+'\n')
        subtitle=folder/'real-recognition.srt'
        out=r.cli('align','subtitle',audio,'--script',script,'--out',subtitle,env=env,
                  label='REAL LOCAL ASR: script-aligned subtitle')
        if out.get('test_stub') is not False:raise RuntimeError('Subtitle unexpectedly used a test stub')
        quality=r.cli('align','check',audio,'--dialogue',EXPECTED,env=env,label='REAL LOCAL ASR: dialogue check')
        if quality.get('ok') is not True:raise RuntimeError('Actual dialogue quality check failed')
        result.update(status='PASS',subtitle=str(subtitle),audio_seconds=duration,
                      timestamp_count=len(stamps),first_char_seconds=stamps[0][0]/1000,
                      last_char_seconds=stamps[-1][1]/1000,speech_segments=segments)
    except Exception as e:
        result.update(status='FAIL',reason=f'{type(e).__name__}: {e}')
    result['elapsed_seconds']=round(time.monotonic()-started,2)
    result['source_unchanged']=hash_file(audio)==before
    if not result['source_unchanged']:result.update(status='FAIL',reason='Source fixture changed')
    json_file(folder/'checkpoint.json',result)
    return result
