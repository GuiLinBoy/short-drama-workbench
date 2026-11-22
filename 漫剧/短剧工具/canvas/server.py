"""Local production canvas. Python stdlib; never imports generation tools.

Only whitelisted project manifests, budgets, task states, logs and referenced
local media/text are read. No credential/environment lookup, provider calls,
Git commands. Writes are limited to user review records and library status metadata. The CLI
binds loopback only; saving a review never starts a production task.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import mimetypes
import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import socket
import sys
import threading
import time
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
DEFAULT_PORT = 8778
sys.path.insert(0, str(HERE.parent/'tools'))
from canvas_review import ReviewStore, ReviewError
from canvas_notify import NotificationStore
from voice_lifecycle import safe_audit as voice_closeout
PROJECT_STATES = {'in_progress', 'completed', 'archived'}
ACTIVE = {'submitting', 'submitted', 'pending', 'queued', 'generating', 'running', 'unknown'}
MEDIA = {'.png', '.jpg', '.jpeg', '.webp', '.mp4', '.mov', '.webm', '.wav', '.mp3', '.ogg', '.flac'}
TEXT = {'.txt', '.md', '.srt'}
PRIVATE = re.compile(r'(^\.|secret|credential|password|api.?key|token|密钥)', re.I)


def clean(value, limit=2000):
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return '接口返回了结构化信息；请在制作工具中核对'
    value = str(value)
    value = re.sub(r'(?i)\b(?:sk-[a-z0-9_-]{10,}|bearer\s+\S+)', '[已隐藏凭据]', value)
    value = re.sub(r'(?i)((?:api[_-]?key|authorization|password|secret|token|密钥)\s*[=:：]\s*)[^\s,;]+', r'\1[已隐藏]', value)
    value = re.sub(r'[A-Za-z0-9+/=]{200,}', '[已省略长编码]', value)
    return value[:limit]


def finite(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def epoch(value):
    try:
        if type(value) in (float, int):
            return float(value)
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0


def opaque(*parts):
    return hashlib.sha256('|'.join(map(str, parts)).encode('utf-8')).hexdigest()[:24]


def apply_clip_timings(clips, reviews, config):
    """Display-only plans; measurements remain bound to an approved current file."""
    config = config if isinstance(config, dict) else {}
    for clip in clips:
        raw = config.get(clip['id'], {})
        raw = raw if isinstance(raw, dict) else {}
        planned = finite(raw.get('planned'))
        clip['timing'] = {'planned': planned if planned and planned > 0 else None, 'adopted': None}
        adopted, video = raw.get('adopted'), clip.get('video') or {}
        if not isinstance(adopted, dict) or clip.get('status') != 'approved' or not video.get('exists'):
            continue
        measured = finite(adopted.get('duration'))
        sha = adopted.get('sha256')
        if not measured or adopted.get('path') != video.get('name') or not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha):
            continue
        matched = any(item.get('subject') == clip['id'] and item.get('kind') == 'video'
            and item.get('status') == 'approved' and any(file.get('path') == adopted['path']
                and file.get('sha256') == sha and file.get('resource', {}).get('exists')
                for file in item.get('files', [])) for item in reviews)
        if matched:
            clip['timing']['adopted'] = {'duration': measured, 'path': adopted['path'], 'sha256': sha}
    values = [(clip['timing']['adopted'] or {}).get('duration') or clip['timing']['planned'] for clip in clips]
    return sum(values) if values and all(value is not None for value in values) else None


def local_path(base: Path, relative, extensions=None):
    if not isinstance(relative, str) or not relative or len(relative) > 2048:
        return None
    wp = PureWindowsPath(relative)
    if wp.drive or wp.is_absolute() or Path(relative).is_absolute() or '://' in relative:
        return None
    if any(PRIVATE.search(part) or part in ('..', '.') for part in wp.parts):
        return None
    path = (base / relative.replace('\\', '/')).resolve()
    if not path.is_relative_to(base.resolve()) or extensions and path.suffix.lower() not in extensions:
        return None
    return path


class Library:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.lock = threading.Lock()
        self.cache = None
        self.cache_at = 0
        self.media = {}
        self.texts = {}
        self.review_stores = {}
        self.notification_stores = {}

    def issue(self, code, message, project=None):
        self.issues.append({'code': code, 'message': clean(message), 'project': project})

    def read_json(self, path, default, required=False):
        if not path.resolve().is_relative_to(self.root):
            self.issue('outside', '已阻止越界文件读取')
            return default
        if not path.is_file():
            if required:
                self.issue('missing', f'{path.name} 尚未建立')
            return default
        try:
            if path.stat().st_size > 2*1024*1024:
                raise ValueError('size')
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(data, type(default)):
                raise ValueError('type')
            return data
        except (ValueError, OSError):
            self.issue('invalid_json', f'{path.name} 无法解析；没有把损坏数据当成完成')
            return default

    def lines(self, path):
        if not path.resolve().is_relative_to(self.root) or not path.is_file():
            return []
        try:
            with path.open('rb') as f:
                size = path.stat().st_size
                start = max(0, size-2*1024*1024)
                f.seek(start)
                if start:
                    f.readline()
                    self.issue('truncated_log', f'{path.name} 仅展示最近日志，不作为完整财务汇总')
                raw = f.read().decode('utf-8-sig').splitlines()[-2000:]
            result, broken = [], 0
            for line in raw:
                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        raise ValueError()
                    result.append(data)
                except ValueError:
                    broken += 1
            if broken:
                self.issue('invalid_log', f'{path.name} 有 {broken} 条损坏记录，已保留原文件')
            return result
        except (OSError, UnicodeError):
            self.issue('unreadable_log', f'{path.name} 读取失败')
            return []

    def ref(self, project, name, text=False):
        if not name:
            return None
        path = local_path(project, name, TEXT if text else MEDIA)
        if path is None:
            self.issue('blocked_ref', '已阻止远程、私密或越界素材引用', project.name)
            return {'exists': False, 'blocked': True, 'name': '不可访问的引用'}
        if not path.is_file():
            return {'exists': False, 'name': clean(name, 180)}
        try:
            stat = path.stat()
            key = opaque(project.name, name, 'text' if text else 'media')
            (self.texts if text else self.media)[key] = (project, path)
            result = {'id': key, 'name': clean(name, 180), 'exists': True,
                      'size': stat.st_size, 'modified': stat.st_mtime}
            if not text:
                result['mime'] = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
                result['url'] = '/api/media/'+key
                if path.suffix.lower() == '.png':
                    with path.open('rb') as f:
                        header = f.read(24)
                    if header[:8] == b'\x89PNG\r\n\x1a\n' and len(header) == 24:
                        w, h = int.from_bytes(header[16:20], 'big'), int.from_bytes(header[20:24], 'big')
                        result.update(width=w, height=h, tiny=w <= 64 and h <= 64 and stat.st_size < 2048)
            return result
        except OSError:
            self.issue('media_changed', '素材在读取期间改变，请稍后刷新', project.name)
            return {'exists': False, 'name': clean(name, 180)}

    def budget(self, project):
        rows = self.read_json(project/'approvals/budget.json', [])
        result = []
        seen = set()
        for r in rows[:200]:
            if not isinstance(r, dict):
                self.issue('budget_invalid', '预算批次记录不是对象', project.name)
                continue
            name = clean(r.get('batch'), 150)
            kind = r.get('kind')
            used, limit = finite(r.get('used', 0)), finite(r.get('limit'))
            pending = r.get('pending_settlements')
            valid = bool(name and name not in seen and kind in ('image', 'video', 'speech')
                         and used is not None and limit is not None and used <= limit)
            seen.add(name)
            if not valid:
                self.issue('budget_invalid', '预算存在重复、越限或无效数值，需在制作工具中核对', project.name)
            result.append({'name': name or '未命名批次', 'kind': clean(kind), 'unit': 'images' if kind == 'image' else 'points',
                           'used': used, 'limit': limit, 'valid': valid,
                           'pending': len(pending) if isinstance(pending, dict) else 0})
        return result

    def tasks(self, project):
        result = []
        for path in sorted(project.glob('tasks/*/attempt-*/state.json'))[:500]:
            if not path.resolve().is_relative_to(project.resolve()):
                self.issue('outside', '已阻止越界任务状态', project.name)
                continue
            s = self.read_json(path, {})
            status = clean(s.get('status', 'invalid'))
            started = epoch(s.get('submitted_at'))
            minutes = max(0, (time.time()-started)/60) if started and status in ACTIVE else None
            ch = s.get('charge') if isinstance(s.get('charge'), dict) else {}
            result.append({'id': opaque(project.name, str(path.parent)), 'name': clean(path.parent.parent.name),
                           'attempt': path.parent.name, 'path': path.parent.relative_to(project).as_posix(),
                           'provider': clean(s.get('provider', 'unknown')), 'status': status,
                           'task_id': clean(s.get('task_id'), 140), 'model': clean(s.get('model')),
                           'active': status in ACTIVE, 'minutes': round(minutes, 1) if minutes is not None else None,
                           'over_60min': minutes is not None and minutes >= 60 and status in ACTIVE,
                           'estimate': finite(s.get('estimate_points', ch.get('points'))),
                           'actual': finite(ch.get('actual_points', s.get('point_cost'))),
                           'images': finite(ch.get('images')), 'batch': clean(ch.get('batch')),
                           'settled': ch.get('settled') is True, 'refunded': ch.get('refunded') is True,
                           'billing_pending': ch.get('settlement_pending') is True or s.get('refund_pending') is True,
                           'error': clean(s.get('error'), 500)})
        return result

    def events(self, project):
        result = []
        for path in sorted((project/'logs').glob('*.jsonl'))[:30]:
            for i, e in enumerate(self.lines(path)):
                changes = e.get('changes')
                note = e.get('note') or (f"{clean(e.get('file'))} · {len(changes)} 项字段变更" if isinstance(changes, dict) else '')
                result.append({'id': opaque(project.name, path.name, i, e.get('time')), 'time': clean(e.get('time'), 80),
                               'epoch': epoch(e.get('time')), 'bot': clean(e.get('bot') or e.get('role') or path.stem),
                               'stage': clean(e.get('stage')), 'event': clean(e.get('event') or e.get('action') or '记录'),
                               'target': clean(e.get('target')), 'note': clean(note, 1500), 'source': path.name})
        return sorted(result, key=lambda e: e['epoch'], reverse=True)[:300]

    def project_status(self, folder):
        path = folder/'协作/作品状态.json'
        if (path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(folder.resolve())):
            raise ReviewError('作品状态文件路径异常')
        if not path.exists():
            return 'in_progress'
        value = json.loads(path.read_text(encoding='utf-8')) if path.stat().st_size <= 4096 else {}
        if (not isinstance(value, dict) or value.get('schemaVersion') != 1
                or not isinstance(value.get('status'), str) or value['status'] not in PROJECT_STATES):
            raise ReviewError('作品状态文件异常，请核对后再修改')
        return value['status']

    def set_project_status(self, key, status, expected):
        if not isinstance(status, str) or status not in PROJECT_STATES or not isinstance(expected, str) or expected not in PROJECT_STATES:
            raise ReviewError('作品状态无效', 400)
        with self.lock:
            works = self.root/'作品'
            candidates = [folder for folder in sorted(works.iterdir())[:200] if folder.is_dir()
                          and not folder.name.startswith('.') and opaque(folder.name) == key
                          and folder.resolve().is_relative_to(works.resolve())
                          and works.resolve().is_relative_to(self.root)
                          and any((folder/n).is_file() for n in ('project.json', 'assets.json', 'clips.json'))]
            if len(candidates) != 1:
                raise ReviewError('作品不存在，请返回作品库重新选择', 404)
            folder = candidates[0]
            current = self.project_status(folder)
            if current == status:
                return {'status': current, 'generationTriggered': False}
            if current != expected:
                raise ReviewError('作品状态已变化，请刷新后再修改')
            path = folder/'协作/作品状态.json'
            if path.parent.is_symlink() or not path.parent.resolve().is_relative_to(folder.resolve()):
                raise ReviewError('作品状态目录异常')
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f'.作品状态-{secrets.token_hex(8)}.tmp')
            try:
                with temporary.open('x', encoding='utf-8') as stream:
                    json.dump({'schemaVersion': 1, 'status': status,
                               'updatedAt': datetime.now(timezone.utc).isoformat()}, stream, ensure_ascii=False)
                    stream.flush(); os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            self.cache_at = 0
            return {'status': status, 'generationTriggered': False}

    def project(self, folder):
        before = len(self.issues)
        p = self.read_json(folder/'project.json', {}, True)
        raw_assets = self.read_json(folder/'assets.json', [], True)
        raw_clips = self.read_json(folder/'clips.json', [], True)
        voices = self.read_json(folder/'voices.json', [])
        title = clean(p.get('title') or folder.name, 100)
        sample = '样例' in folder.name or '样例' in title or p.get('data_kind') == 'sample'
        simulated = p.get('simulated') is True or p.get('data_kind') == 'simulation'
        assets, clips = [], []
        for a in raw_assets[:500]:
            if not isinstance(a, dict):
                continue
            image = self.ref(folder, a.get('image'))
            sheet = self.ref(folder, a.get('sheet'))
            assets.append({'id': clean(a.get('id'), 100), 'name': clean(a.get('name'), 160),
                           'type': clean(a.get('type', 'unknown')), 'status': clean(a.get('status', 'unknown')),
                           'source_asset_id': clean(a.get('source_asset_id')), 'relation': clean(a.get('relation')),
                           'image': image, 'sheet': sheet, 'prompt': self.ref(folder, a.get('prompt'), True),
                           'reference_images': [self.ref(folder, name) for name in a.get('reference_images', [])[:9]
                                                if isinstance(name, str)] if isinstance(a.get('reference_images'), list) else [],
                           'detail': self.ref(folder, a.get('detail'), True),
                           'placeholder': sample and bool(image and image.get('tiny'))})
        for c in raw_clips[:500]:
            if not isinstance(c, dict):
                continue
            d = c.get('draft') if isinstance(c.get('draft'), dict) else {}
            clips.append({'id': clean(c.get('id'), 120), 'episode': clean(c.get('episode'), 60),
                          'duration': finite(c.get('duration')), 'status': clean(c.get('status', 'unknown')),
                          'continue_from': clean(c.get('continue_from')), 'continuity_reason': clean(c.get('continuity_reason')),
                          'assets': [clean(x, 100) for x in c.get('assets', [])[:50]] if isinstance(c.get('assets'), list) else [],
                          'video': self.ref(folder, c.get('video')), 'ending_frame': self.ref(folder, c.get('ending_frame')),
                          'prompt': self.ref(folder, c.get('prompt'), True),
                          'recipe': self.ref(folder, c.get('recipe'), True), 'task_dir': clean(c.get('task_dir')),
                          'draft': {'status': clean(d.get('status', 'planned')), 'video': self.ref(folder, d.get('video')),
                                    'ending_frame': self.ref(folder, d.get('ending_frame')), 'task_dir': clean(d.get('task_dir'))},
                          'shots': [{k: clean(s.get(k), 2000) if k in ('visual', 'dialogue') else finite(s.get(k))
                                     for k in ('start', 'end', 'visual', 'dialogue')}
                                    for s in c.get('shots', [])[:20] if isinstance(s, dict)] if isinstance(c.get('shots'), list) else [],
                          'narration': [{'id': clean(n.get('id')), 'text': clean(n.get('text')), 'start': finite(n.get('start', 0)),
                                        'duration': finite(n.get('narration_duration')), 'audio': self.ref(folder, n.get('audio'))}
                                       for n in c.get('narration', [])[:50] if isinstance(n, dict)] if isinstance(c.get('narration'), list) else []})
        tasks = self.tasks(folder)
        budgets = self.budget(folder)
        events = self.events(folder)
        finals = []
        for report_path in sorted(folder.glob('单集制作/*/成片*.validation.json'))[:50]:
            r = self.read_json(report_path, {})
            video = report_path.with_name(report_path.name.replace('.validation.json', '.mp4'))
            finals.append({'episode': report_path.parent.name, 'video': self.ref(folder, video.relative_to(folder).as_posix()),
                           'recipe': self.ref(folder, r.get('recipe'), True),
                           'duration': finite(r.get('actual_duration')), 'lufs': r.get('measured_lufs') if type(r.get('measured_lufs')) in (float, int) and math.isfinite(r['measured_lufs']) else None,
                           'full_decode': clean(r.get('full_decode')), 'visual_review': clean(r.get('visual_review'))})
        image_ready = sum(a['status'] == 'approved' for a in assets)
        video_ready = sum(c['status'] == 'approved' for c in clips)
        source = p.get('source') if isinstance(p.get('source'), dict) else {}
        source_path = local_path(folder, source.get('path'))
        plan_stages = [
            {'id': 'ingest', 'name': '原文与立项', 'bot': '总导演 / 编剧', 'done': int(bool(source_path and source_path.is_file())), 'total': 1, 'basis': '有原文文件；不代表审核通过'},
            {'id': 'design', 'name': '风格与资产', 'bot': '美术', 'done': len(assets), 'total': max(len(assets), 1), 'basis': '资产清单已登记' if assets else '等待资产清单'},
            {'id': 'storyboard', 'name': '分镜', 'bot': '编剧', 'done': len(clips), 'total': max(len(clips), 1), 'basis': '已登记分镜片段；不推断人工批准'},
            {'id': 'prompts', 'name': '提示词', 'bot': '编剧', 'done': sum(bool(c['prompt'] and c['prompt'].get('exists')) for c in clips), 'total': max(len(clips), 1), 'basis': '按本地提示词文件存在情况'},
            {'id': 'assets', 'name': '参考图审核', 'bot': '美术 / 总导演', 'done': image_ready, 'total': max(len(assets), 1), 'basis': '仅统计 assets.json 的 approved'},
            {'id': 'clips', 'name': '视频片段', 'bot': '摄影', 'done': video_ready, 'total': max(len(clips), 1), 'basis': '仅统计 clips.json 的定稿 approved'},
            {'id': 'assemble', 'name': '后期与成片', 'bot': '剪辑 / 总导演', 'done': 0, 'total': 1, 'basis': '已有成片报告，等待人工验收' if finals else '等待定稿与后期'},
        ]
        point_used = sum(b['used'] for b in budgets if b['unit'] == 'points' and b['used'] is not None)
        image_used = sum(b['used'] for b in budgets if b['unit'] == 'images' and b['used'] is not None)
        if sample:
            self.issue('sample', '样例作品：占位素材与预算数据不代表真实生成或消费批准', folder.name)
        if not voices:
            self.issue('voices_missing', '尚无可展示的音色登记', folder.name)
        modified = max([x.stat().st_mtime for x in (folder/'project.json', folder/'assets.json', folder/'clips.json') if x.is_file()], default=0)
        try:
            store = self.review_stores.setdefault(str(folder), ReviewStore(folder))
            review_records = store.records()
            reviews = store.snapshot(review_records)
            notification_store = self.notification_stores.setdefault(str(folder), NotificationStore(folder))
            notification_store.reviews = store
            reviews['notifications'] = notification_store.snapshot(review_records=review_records, review_snapshot=reviews)
            for item in reviews['items']:
                item['title'] = clean(item['title'], 180)
                item['summary'] = clean(item['summary'], 4000)
                for entry in item['files']:
                    entry['resource'] = self.ref(folder, entry['path'], Path(entry['path']).suffix.lower() in TEXT)
                    if 'text' in entry:
                        entry['text'] = clean(entry['text'], 100_000)
                if item['request']:
                    item['request']['prompt'] = clean(item['request']['prompt'], 16000)
                for entry in item['history']:
                    entry['note'] = clean(entry['note'], 4000)
                    entry.pop('content', None)
            for entry in reviews['history']:
                entry['note'] = clean(entry['note'], 4000)
                entry.pop('content', None)
        except (ReviewError, OSError, ValueError, KeyError, TypeError) as error:
            self.issue('review_invalid', '审核记录或送审材料异常，已停止该作品的审核操作', folder.name)
            reviews = {'items': [], 'history': [], 'error': '审核材料或记录校验失败，请保留现场并核对', 'recordCount': 0}
        voices_pending = voice_closeout(folder, None if reviews.get('error') else reviews['items'])
        for error in voices_pending['errors']:
            self.issue('voice_closeout_invalid', clean(error, 1000), folder.name)
        for item in voices_pending['characters']:
            if not item['complete']:
                self.issue('voice_closeout', clean(f'{item["name"]}：{item["action"]}（来源 {item["sourceClip"]}）', 1000), folder.name)
        plan_stages.insert(6, {'id': 'voice_closeout', 'name': '首次开口后的音色收尾', 'bot': '摄影 / 总导演',
            'done': sum(item['complete'] for item in voices_pending['characters']),
            'total': len(voices_pending['characters']) + int(bool(voices_pending['errors'])),
            'basis': ('声音来源核对异常，请查看数据提示' if voices_pending['errors'] else
                      '；'.join(f'{item["name"]}：{item["action"]}' for item in voices_pending['characters']) or
                      '暂无已通过的角色开口视频；首次通过后开始收尾')})
        creative = self.read_json(folder/'创作画布.json', {})
        creative = creative if isinstance(creative, dict) else {}
        planned_duration = apply_clip_timings(clips, reviews['items'], creative.get('clipTimings'))
        raw_stories = creative.get('stories', [])
        asset_ids = {a['id'] for a in assets}
        storyboards = []
        for entry in (raw_stories if isinstance(raw_stories, list) else [])[:200]:
            if not isinstance(entry, dict):
                continue
            storyboards.append({'id': clean(entry.get('id'), 120), 'title': clean(entry.get('title'), 200),
                'episode': clean(entry.get('episode'), 60), 'duration': finite(entry.get('duration')),
                'start': finite(entry.get('start')), 'end': finite(entry.get('end')), 'paper': True,
                'review_subject': clean(entry.get('review_subject'), 120),
                'assets': [x for x in entry.get('assets', []) if isinstance(x, str) and x in asset_ids]
                          if isinstance(entry.get('assets'), list) else [],
                'shots': [{k: clean(shot.get(k), 2000) if k in ('visual', 'dialogue') else finite(shot.get(k))
                           for k in ('start', 'end', 'visual', 'dialogue')}
                          for shot in entry.get('shots', [])[:20] if isinstance(shot, dict)]
                          if isinstance(entry.get('shots'), list) else []})
        links = creative.get('assetReferences', {})
        links = links if isinstance(links, dict) else {}
        asset_links = {key: [x for x in value[:20] if isinstance(x, str) and x in asset_ids and x != key]
                       for key, value in links.items() if key in asset_ids and isinstance(value, list)}
        prompts, refs = creative.get('displayPrompts', {}), creative.get('referenceImages', {})
        aliases = creative.get('assetAliases', {})
        aliases = {key: value for key, value in aliases.items()
                   if isinstance(key, str) and len(key) <= 120 and isinstance(value, str)
                   and value in (asset_ids | {clip['id'] for clip in clips}) and key != value} if isinstance(aliases, dict) else {}
        clip_ids = {clip['id'] for clip in clips}
        clip_images = creative.get('clipReferenceImages', {})
        clip_images = {key: value for key, value in clip_images.items()
                       if key in clip_ids and isinstance(value, str) and value in asset_ids} if isinstance(clip_images, dict) else {}
        withdrawn = creative.get('withdrawnVideoThrough', {})
        review_by_id = {item['id']: item for item in reviews['items']}
        withdrawn = {key: value for key, value in withdrawn.items()
                     if key in clip_ids and isinstance(value, str) and value in review_by_id
                     and review_by_id[value]['subject'] == key
                     and review_by_id[value]['kind'] in ('video_prompt', 'video')} if isinstance(withdrawn, dict) else {}
        internal_assets = creative.get('internalAssets', [])
        internal_assets = [key for key in internal_assets if isinstance(key, str) and key in asset_ids] if isinstance(internal_assets, list) else []
        prompts = prompts if isinstance(prompts, dict) else {}
        refs = refs if isinstance(refs, dict) else {}
        for asset in assets:
            if isinstance(prompts.get(asset['id']), str):
                asset['prompt'] = self.ref(folder, prompts[asset['id']], True)
            if isinstance(refs.get(asset['id']), list):
                asset['reference_images'] = [self.ref(folder, path) for path in refs[asset['id']][:9] if isinstance(path, str)]
        try:
            library_status = self.project_status(folder)
        except (ReviewError, OSError, ValueError, UnicodeError):
            library_status = 'in_progress'
            self.issue('project_status_invalid', '作品状态无法读取，请核对作品状态文件', folder.name)
        return {'key': opaque(folder.name), 'folder': clean(folder.name), 'title': title, 'library_status': library_status,
                'genre': clean(p.get('genre', '未设置')), 'mode': clean(p.get('mode', 'series')), 'ratio': clean(p.get('ratio', '未设置')),
                'classification': 'sample' if sample else 'simulation' if simulated else 'unclassified',
                'assets': assets, 'clips': clips, 'storyboards': storyboards, 'creative_asset_references': asset_links,
                'creative_asset_aliases': aliases,
                'creative_clip_reference_images': clip_images,
                'creative_withdrawn_video_through': withdrawn,
                'creative_internal_assets': internal_assets,
                'tasks': tasks, 'budgets': budgets, 'events': events, 'finals': finals, 'reviews': reviews,
                'voice_count': len(voices), 'voice_closeout': voices_pending, 'pipeline': plan_stages, 'modified': modified,
                'counts': {'assets': len(assets), 'asset_approved': image_ready, 'clips': len(clips), 'clip_approved': video_ready,
                           'active_tasks': sum(t['active'] for t in tasks), 'duration': sum(c['duration'] or 0 for c in clips),
                           'planned_duration': planned_duration,
                           'points_used': round(point_used, 2), 'images_used': image_used},
                'issues': self.issues[before:].copy()}

    def snapshot(self, force=False):
        with self.lock:
            if not force and self.cache is not None and time.monotonic()-self.cache_at < 1:
                return self.cache
            self.issues, self.media, self.texts = [], {}, {}
            works = self.root/'作品'
            projects = []
            if works.is_dir() and works.resolve().is_relative_to(self.root):
                for folder in sorted(works.iterdir())[:200]:
                    if folder.is_dir() and not folder.name.startswith('.'):
                        if not folder.resolve().is_relative_to(works.resolve()):
                            self.issue('outside', '已阻止越界作品目录')
                            continue
                        if any((folder/n).is_file() for n in ('project.json', 'assets.json', 'clips.json')):
                            projects.append(self.project(folder))
            else:
                self.issue('works_missing', '作品目录尚未建立；画布不会自动创建它')
            registry_exists = (self.root/'作品清单.json').is_file()
            if registry_exists:
                registry = self.read_json(self.root/'作品清单.json', [])
                for entry in registry[:200]:
                    if not isinstance(entry, dict):
                        continue
                    # The registry may annotate discovered projects, never expand
                    # file access to a path named by untrusted registry contents.
                    matches = [p for p in projects if p['folder'] == entry.get('name')]
                    if len(matches) == 1:
                        matches[0]['registry'] = {k: clean(entry.get(k), 200) for k in ('status', 'stage', 'waiting_on')}
            if not registry_exists:
                self.issue('registry_missing', '作品清单尚未建立，当前按作品目录自动发现')
            names = {p['folder'] for p in projects}
            ledger = []
            for i, e in enumerate(self.lines(self.root/'全局账本.jsonl')):
                project = clean(e.get('project'), 100)
                old_mock = (project in ('验收-第4组', '验收-调试') and str(e.get('time', '')).startswith('2026-11-05')
                            and e.get('provider_event') == 'indextts')
                simulated = old_mock or e.get('simulated') is True or e.get('source') == 'SIMULATED'
                ledger.append({'id': opaque(i, e.get('time'), project), 'time': clean(e.get('time'), 80), 'project': project,
                               'provider': clean(e.get('provider') or e.get('provider_event')), 'event': clean(e.get('event')),
                               'batch': clean(e.get('batch')), 'points': finite(e.get('actual_points') if e.get('event') == 'settle' else e.get('points')),
                               'images': finite(e.get('images')), 'simulated': simulated, 'orphan': project not in names})
            settings = self.read_json(self.root/'全局设置.json', {})
            payload = {'projects': projects, 'issues': self.issues, 'ledger': ledger[-500:][::-1],
                       'registry_exists': registry_exists, 'discovery': 'directory-scan',
                       'balance': {'value': None, 'status': '未查询', 'note': '画布不调用账户接口，也不使用历史账本余额'},
                       'limits': {'h3': finite((settings.get('h3') or {}).get('max_running_tasks')) if isinstance(settings.get('h3'), dict) else None,
                                  'image': finite((settings.get('nexorai') or {}).get('max_running')) if isinstance(settings.get('nexorai'), dict) else None},
                       'read_only': True, 'external_calls': False, 'history_mock_count': sum(e['simulated'] for e in ledger)}
            revision = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()[:20]
            payload['revision'] = revision
            self.cache, self.cache_at = payload, time.monotonic()
            return payload

    def resource(self, key, text=False):
        self.snapshot()
        if not re.fullmatch(r'[a-f0-9]{24}', key):
            return None
        with self.lock:
            item = (self.texts if text else self.media).get(key)
        if item is None:
            return None
        base, path = item
        if not path.resolve().is_relative_to(base.resolve()) or not path.resolve().is_relative_to(self.root) or not path.is_file():
            return None
        return path


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows SO_REUSEADDR permits a second listener on the same address.
    # Use exclusive binding there; retain normal restart behavior elsewhere.
    allow_reuse_address = not hasattr(socket, 'SO_EXCLUSIVEADDRUSE')

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, library, dist=None, reviews_enabled=True):
        self.library = library
        self.reviews_enabled = reviews_enabled
        self.review_token = secrets.token_urlsafe(32)
        self.dist = Path(dist or HERE/'dist').resolve()
        self.stream_slots = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def allowed(self):
        host = self.headers.get('Host', '')
        valid = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if host not in valid:
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {'http://'+h for h in valid}:
            return False
        if self.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
            # A user may open the static shell from a bookmark or browser extension.
            # Keep all data/media endpoints same-origin, including direct navigation.
            return (not origin and urlsplit(self.path).path in ('/', '/index.html')
                    and self.headers.get('Sec-Fetch-Mode') == 'navigate'
                    and self.headers.get('Sec-Fetch-Dest') == 'document')
        return True

    def send_headers(self, code, content_type, length=None, extra=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        if length is not None:
            self.send_header('Content-Length', str(length))
        for k, v in (extra or {}).items():
            self.send_header(k, str(v))
        self.end_headers()

    def json(self, value, code=200, extra=None):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_headers(code, 'application/json; charset=utf-8', len(data), extra)
        if self.command != 'HEAD':
            self.wfile.write(data)

    def do_POST(self):
        self.close_connection = True
        operation = urlsplit(self.path).path
        if operation not in ('/api/reviews', '/api/review-notifications', '/api/project-status'):
            self.reject_write()
            return
        if (not self.allowed() or self.headers.get('Origin') != 'http://'+self.headers.get('Host', '')
                or not secrets.compare_digest(self.headers.get('X-Canvas-Review', ''), self.server.review_token)):
            self.json({'error': '审核写入仅限本机画布同源操作，请刷新页面'}, 403)
            return
        if not self.server.reviews_enabled:
            self.json({'error': '当前画布以只读模式运行'}, 403)
            return
        try:
            require_length = self.headers.get('Content-Length', '')
            if (self.headers.get('Transfer-Encoding') or not require_length.isdecimal()
                    or not 1 <= int(require_length) <= 64_000
                    or self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'):
                self.json({'error': '审核请求格式或长度无效'}, 400)
                return
            self.connection.settimeout(5)
            body = json.loads(self.rfile.read(int(require_length)))
            if operation == '/api/project-status':
                if not isinstance(body, dict) or set(body) != {'project', 'status', 'expectedStatus'}:
                    self.json({'error': '作品状态请求字段无效'}, 400)
                    return
                self.json(self.server.library.set_project_status(body['project'], body['status'], body['expectedStatus']))
                return
            field = 'decision' if operation == '/api/reviews' else 'notification'
            if not isinstance(body, dict) or set(body) != {'project', field}:
                self.json({'error': '审核请求字段无效'}, 400)
                return
            library = self.server.library
            # Rebuild before writing; never take a filesystem path from the browser.
            with library.lock:
                library.cache_at = 0
            projects = library.snapshot(force=True)['projects']
            project = next((p for p in projects if p['key'] == body['project']), None)
            if project is None or project['reviews'].get('error'):
                self.json({'error': '作品不存在或审核记录异常'}, 409)
                return
            key = str(library.root/'作品'/project['folder'])
            result = (library.review_stores[key].decide(body['decision']) if field == 'decision'
                      else library.notification_stores[key].enqueue(body['notification']))
            with library.lock:
                library.cache_at = 0
            self.json(result)
        except ReviewError as error:
            self.json({'error': str(error)}, error.code)
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            self.json({'error': '审核未确认保存成功，请刷新核对记录后再操作'}, 400)

    def reject_write(self):
        self.close_connection = True
        self.json({'error': '仅支持专用审核和作品状态接口，不提供生成或制作文件写入'}, 405, {'Allow': 'GET, HEAD'})

    do_PUT = reject_write
    do_PATCH = reject_write
    do_DELETE = reject_write
    do_OPTIONS = reject_write

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.allowed():
            self.json({'error': '仅限本机同源访问'}, 403)
            return
        path = unquote(urlsplit(self.path).path)
        try:
            if path == '/api/snapshot':
                data = {**self.server.library.snapshot(), 'read_only': not self.server.reviews_enabled,
                        'review_writes': self.server.reviews_enabled, 'production_execution': False}
                tag = '"'+opaque(data['revision'], self.server.review_token, self.server.reviews_enabled)+'"'
                if self.headers.get('If-None-Match') == tag:
                    self.send_headers(304, 'application/json', 0, {'ETag': tag})
                else:
                    self.json(data, extra={'ETag': tag, 'X-Canvas-Review': self.server.review_token})
            elif path == '/api/events':
                if self.command == 'HEAD':
                    self.send_headers(200, 'text/event-stream', 0)
                else:
                    self.events()
            elif path.startswith('/api/media/'):
                media = self.server.library.resource(path.removeprefix('/api/media/'))
                self.file(media) if media else self.json({'error': '素材不存在或不可访问'}, 404)
            elif path.startswith('/api/text/'):
                media = self.server.library.resource(path.removeprefix('/api/text/'), True)
                if not media:
                    self.json({'error': '文本不存在或不可访问'}, 404)
                elif media.stat().st_size > 100_000:
                    self.json({'error': '文本过长，请在制作目录中查看'}, 413)
                else:
                    self.json({'name': clean(media.name), 'text': clean(media.read_text(encoding='utf-8-sig'), 100_000)})
            elif path.startswith('/api/'):
                self.json({'error': '未提供此接口'}, 404)
            else:
                relative = 'index.html' if path == '/' else path.lstrip('/')
                static = local_path(self.server.dist, relative, {'.html', '.js', '.css', '.woff', '.woff2', '.png', '.ico'})
                if not static or not static.is_file():
                    self.json({'error': '页面资源不存在；首次使用请先 npm run build'}, 404)
                else:
                    self.file(static, media=False)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except (OSError, UnicodeError, ValueError, TypeError):
            self.json({'error': '本地文件正在改变或格式异常，请刷新；没有修改原文件'}, 503)

    def file(self, path, media=True):
        size = path.stat().st_size
        start, end, code = 0, size-1, 200
        value = self.headers.get('Range') if media else None
        if value:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
            if not match or not any(match.groups()) or not size:
                self.json({'error': '无效的字节范围'}, 416, {'Content-Range': f'bytes */{size}'})
                return
            a, b = match.groups()
            if a:
                start, end = int(a), min(int(b), size-1) if b else size-1
            else:
                start, end = max(0, size-int(b)), size-1
            if start > end or start >= size:
                self.json({'error': '字节范围越界'}, 416, {'Content-Range': f'bytes */{size}'})
                return
            code = 206
        extra = {'Accept-Ranges': 'bytes'} if media else {}
        if code == 206:
            extra['Content-Range'] = f'bytes {start}-{end}/{size}'
        kind = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
        self.send_headers(code, kind, max(0, end-start+1), extra)
        if self.command == 'HEAD':
            return
        with path.open('rb') as f:
            f.seek(start)
            left = end-start+1
            while left > 0:
                data = f.read(min(left, 256*1024))
                if not data:
                    break
                self.wfile.write(data)
                left -= len(data)

    def events(self):
        if not self.server.stream_slots.acquire(blocking=False):
            self.json({'error': '实时连接数已满'}, 429)
            return
        self.close_connection = True
        self.send_headers(200, 'text/event-stream; charset=utf-8', extra={'Connection': 'close'})
        last = None
        try:
            # Bounded lifetime lets the browser reconnect and the server exit cleanly.
            for _ in range(60):
                revision = self.server.library.snapshot()['revision']
                data = f'data: {revision}\n\n' if last != revision else ': heartbeat\n\n'
                self.wfile.write(data.encode())
                self.wfile.flush()
                last = revision
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self.server.stream_slots.release()


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=HERE.parent.parent, help='漫剧数据根目录')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help='正式画布固定使用8778')
    parser.add_argument('--preview', action='store_true', help='仅隔离预览允许明确指定其他端口，须使用独立数据根')
    parser.add_argument('--read-only', action='store_true', help='禁止新增审核记录，仅查看')
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('port 须在 1024–65535 内')
    if args.port != DEFAULT_PORT and not args.preview:
        parser.error('正式画布固定使用8778，不自动更换端口；隔离预览须加 --preview 并指定独立 --data-root')
    if args.preview and args.data_root.resolve() == HERE.parent.parent.resolve():
        parser.error('隔离预览必须指定独立 --data-root，不能使用正式数据根')
    return args


def main():
    args = parse_arguments()
    try:
        server = Server(('127.0.0.1', args.port), Library(args.data_root), reviews_enabled=not args.read_only)
    except OSError:
        raise SystemExit(f'无法启动画布：端口 {args.port} 已被占用或不可用。若画布已启动，请打开 '
                         f'http://127.0.0.1:{args.port}/；否则先处理端口占用再重试。未自动更换端口。') from None
    print(f'漫剧制作控制室（本地审核，不启动生成） http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
