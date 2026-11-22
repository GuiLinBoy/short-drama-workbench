"""Offline delivery checks; no upload, approval, rendering or licence acquisition.

An explicit preview remains a preview even if its files were written successfully.
Publication intent is separate from the legacy project.publish rendering flag.
"""
import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace


def publication_context(project, purpose=None):
    if not isinstance(project,dict):
        raise ValueError('project.json 顶层必须是对象')
    publish = project.get('publish', False)
    if type(publish) is not bool:
        raise ValueError('publish 必须为布尔值')
    intent = project.get('delivery_intent', {})
    if not isinstance(intent, dict):
        raise ValueError('delivery_intent 必须是对象')
    if any(key in intent and (not isinstance(intent[key],str) or not intent[key].strip()) for key in ('platform','use')):
        raise ValueError('delivery_intent 的 platform/use 必须是明确的非空文本')
    intended = intent.get('purpose')
    choices = ('preview', 'public-release')
    if intended is not None and intended not in choices:
        raise ValueError('delivery_intent.purpose 必须是 preview 或 public-release')
    if purpose is not None and purpose not in choices:
        raise ValueError('交付用途必须是 preview 或 public-release')
    public_requested = publish or intended == 'public-release'
    return {'purpose': purpose or ('public-release' if public_requested else 'preview'),
            'public_requested': public_requested,
            'platform': intent.get('platform'), 'use': intent.get('use'),
            'explicit_preview': purpose == 'preview'}


def inspect_delivery(root, video, purpose='public-release'):
    # Import here so assemble can reuse publication_context without a cycle.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import assemble
    root = Path(root).resolve()
    video = assemble.contained(root, video)
    project = json.loads((root / 'project.json').read_text(encoding='utf-8-sig'))
    context = publication_context(project, purpose)
    report_path = video.with_suffix('.validation.json')
    result = {'video': str(video), 'purpose': context['purpose'], 'context': context,
              'errors': [], 'automaticPass': False,
              'releaseStatus': 'blocked', 'manualReview': 'existing_final_review_required'}
    errors = result['errors']
    if not video.is_file() or not report_path.is_file():
        errors.append('缺少成片或匹配的合成报告；文件存在不能冒充完整交付')
        return result
    report = json.loads(report_path.read_text(encoding='utf-8-sig'))
    delivery = report.get('delivery', report.get('publication', {}))
    if delivery.get('status') != 'complete' or delivery.get('video_sha256') != assemble.digest(video):
        errors.append('成片与合成完成记录的指纹不匹配')
    srt = video.with_suffix('.srt')
    if not srt.is_file() or delivery.get('srt_sha256') != assemble.digest(srt):
        errors.append('字幕与合成完成记录的指纹不匹配')
    if report.get('full_decode') != 'passed':
        errors.append('缺少该版本完整解码通过记录')
    if context['purpose'] == 'public-release':
        if delivery.get('purpose') == 'preview' or report.get('local_preview_without_visible_ai_label'):
            errors.append('该版本明确为本地预览，不能作为可发布版本交付')
        if report.get('steps', {}).get('burn_in', {}).get('aigc_label') is not True or report.get('aigc_metadata') is not True:
            errors.append('成片缺少本地发布规范要求的可见或元数据 AI 标识')
        music = report.get('music')
        if music:
            path = music.get('path')
            try:
                if not path or report.get('input_sha256', {}).get(path) != assemble.digest(Path(path)):
                    errors.append('混入成片的配乐缺少可核对的来源指纹')
                else:
                    assemble.music_for(SimpleNamespace(music=path), root, publish=True, usage=context)
            except (SystemExit, OSError, ValueError, KeyError):
                errors.append('配乐的当前授权、用途、来源或证据尚未完整核实')
    result['automaticPass'] = not errors
    if not errors:
        result['releaseStatus'] = ('technical_checks_passed_review_required'
                                   if context['purpose'] == 'public-release' else 'local_preview_only')
    result['note'] = '复用既有成片验收；本工具不新增用户确认，也不把技术通过写成已经听看或允许发布。'
    return result


def inspect_music(root, music, purpose='public-release'):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import assemble
    root=Path(root).resolve()
    project=json.loads((root/'project.json').read_text(encoding='utf-8-sig'))
    context=publication_context(project,purpose)
    result={'context':context,'automaticPass':False,'errors':[]}
    try:
        _,record=assemble.music_for(SimpleNamespace(music=str(music)),root,
                                    publish=context['purpose']=='public-release',usage=context)
        result.update(automaticPass=True,music=record)
    except (OSError,ValueError,KeyError,SystemExit):
        result['errors'].append('候选配乐的来源、实际文件、授权证据或当前使用范围未核实')
    result['note']='选曲前核验已有证据；不是新批准，也不自动取得授权。'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path)
    subject=parser.add_mutually_exclusive_group(required=True)
    subject.add_argument('--video', type=Path)
    subject.add_argument('--music', type=Path, help='选曲/混音前仅核验候选曲的当前授权范围')
    parser.add_argument('--purpose', choices=('preview', 'public-release'), default='public-release')
    args = parser.parse_args()
    try:
        result = (inspect_delivery(args.project, args.video, args.purpose) if args.video is not None
                  else inspect_music(args.project,args.music,args.purpose))
    except (OSError, ValueError, TypeError, KeyError) as error:
        result = {'automaticPass': False, 'releaseStatus': 'blocked', 'errors': [str(error)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['automaticPass'] else 1


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
