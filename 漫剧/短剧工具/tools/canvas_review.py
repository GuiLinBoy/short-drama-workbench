"""Local canvas review packets and append-only human decisions. No provider/network.

Exact frames of approved videos may be decoded locally to verify provenance.

The journal is not an authentication boundary against another local process.
Only the canvas HTTP handler accepts human decisions. Explicit, project-scoped
delegation permits a separately attributed director decision with bound evidence.
Legacy director status is not imported.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import threading
import time
import uuid

BASE = '协作/审核'
TEXT = {'.txt', '.md', '.srt'}
MEDIA = {'.png', '.jpg', '.jpeg', '.webp', '.mp4', '.mov', '.webm', '.wav', '.mp3', '.ogg', '.flac'}
PRIVATE = re.compile(r'(^\.|secret|credential|password|api.?key|token|密钥)', re.I)
KINDS = {'plan': ('制作方案与预算', 1), 'storyboard': ('分镜', 2),
         'image_prompt': ('图片提示词', 2), 'image': ('生成图片', 3),
         'audio': ('声音样本', 3), 'video_prompt': ('视频提示词', 4),
         'video': ('视频片段', 5), 'final': ('完整成片', 6), 'exception': ('例外申请', 0)}
REQUIRED = {'storyboard': {'plan'}, 'image_prompt': {'plan'}, 'image': {'image_prompt'},
            'video_prompt': {'plan', 'storyboard'}, 'video': {'video_prompt'}, 'final': {'video'}}
ACTIONS = {'approve', 'request_changes', 'defer', 'resume', 'comment'}
_LOCKS = {}
_LOCKS_LOCK = threading.Lock()


class ReviewError(ValueError):
    def __init__(self, message, code=409):
        super().__init__(message)
        self.code = code


def require(condition, message, code=409):
    if not condition:
        raise ReviewError(message, code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read_json(path):
    require(path.stat().st_size <= 2*1024*1024, '审核文件过大，已停止读取')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def inside(root, name, extensions=None):
    require(isinstance(name, str) and 0 < len(name) <= 2048, '无效的作品内文件引用', 400)
    wp = PureWindowsPath(name)
    require(not (wp.drive or wp.is_absolute() or Path(name).is_absolute() or '://' in name)
            and not any(p in ('.', '..') or PRIVATE.search(p) for p in wp.parts),
            '审核仅允许非私密的作品内相对路径', 400)
    path = (root / name.replace('\\', '/')).resolve()
    require(path.is_relative_to(root.resolve()), '审核文件越出作品目录', 400)
    require(not any(PRIVATE.search(p) for p in path.relative_to(root).parts), '审核不能读取私密文件链接', 400)
    require(not extensions or path.suffix.lower() in extensions, '审核文件类型不支持', 400)
    return path


def relative(root, value):
    path = Path(value)
    if path.is_absolute():
        require(path.resolve().is_relative_to(root), '生成输入越出作品目录', 400)
        value = path.resolve().relative_to(root).as_posix()
    return inside(root, value).relative_to(root).as_posix()


def text(value, limit, name, required=False):
    require(isinstance(value, str) and len(value) <= limit and (not required or value.strip()),
            f'{name}为空或过长', 400)
    return value.strip()


class ReviewStore:
    RECORD_TYPES = ('packet', 'decision')
    def __init__(self, root):
        self.root = Path(root).resolve()
        require(self.root.is_dir(), '作品目录不存在', 400)
        self.base = self.root / BASE
        self.journal = self.base / '记录'
        self._hash_cache = {}
        self._frame_cache = {}
        self._read_cache = {}
        self._binary_cache = {}

    @staticmethod
    def _version(path):
        stat = path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def _read_cached(self, path, limit):
        # Boundary checks remain at each caller. Cache only complete, stable reads;
        # replacement, truncation and edits all invalidate the file identity.
        version = self._version(path)
        require(version[2] <= limit, '审核文件过大，已停止读取', 400)
        key = (str(path), version)
        raw = self._read_cache.get(key)
        if raw is None:
            raw = path.read_bytes()
            require(self._version(path) == version and len(raw) == version[2], '审核文件正在改变，请刷新')
            if len(self._read_cache) >= 256:
                self._read_cache.clear()
            self._read_cache[key] = raw
        return raw

    def _checked_ffmpeg(self, runtime):
        from authorized_cli import checked_binary
        entry = runtime['ffmpeg']
        path = Path(entry['path']).resolve()
        version = self._version(path)
        key = (str(path), version, digest(runtime))
        program = self._binary_cache.get(key)
        if program is None:
            program = checked_binary(entry['path'], runtime)
            require(self._version(path) == version, '审核运行工具正在改变，请刷新')
            if len(self._binary_cache) >= 16:
                self._binary_cache.clear()
            self._binary_cache[key] = program
        return program

    def _boundary(self):
        for path in (self.base.parent, self.base, self.journal):
            require(path.resolve().is_relative_to(self.root) and not path.is_symlink(), '审核目录不能是外部链接')

    @contextmanager
    def _write_lock(self):
        self._boundary()
        with _LOCKS_LOCK:
            lock = _LOCKS.setdefault(str(self.root), threading.RLock())
        with lock:
            self.journal.mkdir(parents=True, exist_ok=True)
            marker = self.base / '写入.lock'
            end = time.monotonic() + 3
            while True:
                try:
                    fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    break
                except FileExistsError:
                    require(time.monotonic() < end, '审核记录正在写入；请稍后重试，不会自动删除锁')
                    time.sleep(.03)
            try:
                self._boundary()
                yield
            finally:
                os.close(fd)
                marker.unlink()

    def records(self):
        self._boundary()
        if not self.journal.exists():
            return []
        paths = sorted(self.journal.glob('*.json'))
        require(len(paths) <= 10000, '审核记录达到读取上限，需扩展后继续；未清理历史')
        records, previous = [], '0'*64
        for seq, path in enumerate(paths, 1):
            require(not path.is_symlink() and path.resolve().is_relative_to(self.root), '审核记录路径异常')
            record = read_json(path)
            require(isinstance(record, dict) and record.get('schemaVersion') == 1 and record.get('seq') == seq
                    and record.get('previous') == previous and record.get('type') in self.RECORD_TYPES,
                    '审核记录链损坏；已停止写入和批准')
            fingerprint = digest(record)
            require(path.name == f'{seq:08d}-{fingerprint}.json', '审核记录校验失败；已停止写入和批准')
            records.append(record)
            previous = fingerprint
        return records

    def _append(self, records, kind, body):
        require(len(records) < 10000, '审核记录达到上限，需扩展后继续；未清理历史')
        record = {'schemaVersion': 1, 'seq': len(records)+1,
                  'previous': digest(records[-1]) if records else '0'*64,
                  'time': datetime.now(timezone.utc).isoformat(timespec='milliseconds'), 'type': kind, **body}
        raw = canonical(record)
        require(len(raw) <= 2*1024*1024, '审核记录过大；请减少本批数量', 400)
        target = self.journal / f'{record["seq"]:08d}-{digest(record)}.json'
        temporary = self.journal / f'.{uuid.uuid4().hex}.tmp'
        try:
            with temporary.open('xb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # Commit one complete transaction atomically, without replacing a file.
            os.link(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return record

    def _file(self, entry):
        path = inside(self.root, entry['path'], TEXT | MEDIA)
        label = entry.get('label', path.name)
        if not path.is_file():
            return {**entry, 'label': label, 'exists': False, 'sha256': None}
        stat = path.stat()
        cache_key = (str(path), (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        sha = self._hash_cache.get(cache_key)
        if sha is None:
            hasher = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024*1024), b''):
                    hasher.update(block)
            after = path.stat()
            require((after.st_size, after.st_mtime_ns, after.st_ctime_ns) ==
                    (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns), '审核素材正在改变，请刷新')
            sha = hasher.hexdigest()
            require(self._version(path) == cache_key[1], '审核素材正在改变，请刷新')
            if len(self._hash_cache) >= 1024:
                self._hash_cache.clear()
            self._hash_cache[cache_key] = sha
        result = {**entry, 'label': label, 'exists': True, 'sha256': sha, 'size': stat.st_size}
        if path.suffix.lower() in TEXT:
            require(stat.st_size <= 100_000, '送审文本超过100KB，请拆分后提交', 400)
            raw = self._read_cached(path, 100_000)
            after = path.stat()
            require((after.st_size, after.st_mtime_ns, after.st_ctime_ns) ==
                    (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns), '送审文本正在改变，请刷新')
            result.update(text=raw.decode('utf-8-sig'), sha256=hashlib.sha256(raw).hexdigest())
        require(self._version(path) == cache_key[1], '审核素材正在改变，请刷新')
        return result

    def _request(self, packet):
        if not packet.get('request'):
            return None, []
        path = inside(self.root, packet['request'], {'.json'})
        require(path.relative_to(self.root).parts[0] == 'requests', '只允许 requests/ 下的正式生成请求', 400)
        require(path.is_file(), '待审生成请求不存在')
        raw = self._read_cached(path, 200_000)
        require(len(raw) <= 200_000, '待审生成请求过大', 400)
        request = json.loads(raw)
        tool = packet.get('generation', {}).get('tool')
        require(request.get('provider') == {'h3': 'minimax-h3', 'image': 'nexorai'}.get(tool), '待审请求与生成渠道不匹配', 400)
        prompt = text(request.get('prompt'), 16000, '实际提交提示词', True)
        params = request.get('params')
        allowed = {'model', 'duration', 'ratio', 'resolution', 'use_context_ir', 'mute_audio',
                   'aigc_watermark', 'skill_id', 'n', 'aspect_ratio', 'quality', 'output_format', 'size'}
        require(isinstance(params, dict) and set(params) <= allowed
                and all(type(v) in (str, int, float, bool) for v in params.values()), '生成参数存在未支持字段', 400)
        refs = request.get('references', [])
        require(isinstance(refs, list) and len(refs) <= 12, '生成参考素材数量异常', 400)
        files = []
        if request.get('prompt_source'):
            files.append({'path': relative(self.root, request['prompt_source']), 'label': '原始制作提示词'})
        ref_paths, reference_hashes = [], {}
        for ref in refs:
            require(isinstance(ref, dict) and isinstance(ref.get('path'), str), '参考素材路径缺失', 400)
            name = relative(self.root, ref['path'])
            ref_paths.append(name)
            require(isinstance(ref.get('sha256'), str) and re.fullmatch('[a-f0-9]{64}', ref['sha256']), '参考素材缺少准备时的SHA256', 400)
            reference_hashes[name] = ref['sha256']
            files.append({'path': name, 'label': text(ref.get('label') or ref.get('role') or '生成参考素材', 160, '参考素材名称')})
        projected = {'path': packet['request'], 'sha256': hashlib.sha256(raw).hexdigest(), 'prompt': prompt,
                     'params': params, 'referencePaths': ref_paths, 'referenceHashes': reference_hashes,
                     'warnings': request.get('warnings', [])}
        require(isinstance(projected['warnings'], list) and all(isinstance(x, str) for x in projected['warnings']), '请求警告格式异常', 400)
        return projected, files

    def _content(self, packet):
        request, extra = self._request(packet)
        if packet.get('storyboard'):
            extra.append({'path': packet['storyboard'], 'label': '本段分镜（随视频提示词确认）'})
        if packet.get('draft'):
            extra.append({'path': packet['draft']['prompt'], 'label': '完整视频提示词草稿（尚非正式请求）'})
            extra.extend({'path': ref['path'], 'label': ref['label']}
                         for ref in packet['draft'].get('references', []) if ref.get('path'))
        for entry in packet.get('reusedReferences', []):
            extra.append({'path': entry['authorization'], 'label': '已有素材复用依据（沿用用户原话）'})
        entries = {item['path']: item for item in packet['files']}
        for item in extra:
            entries.setdefault(item['path'], item)
        files = [self._file(item) for item in entries.values()]
        return {'files': files, 'request': request}

    def _reuse_coverage(self, packet, content):
        """Bind explicit prior reuse evidence to this pending prompt, not a decision.

        An approved legacy manifest alone is insufficient. The cited user quote,
        asset identity and exact bytes travel with the new prompt for inspection.
        No historical canvas approval is created or imported.
        """
        covered = set()
        entries = packet.get('reusedReferences', [])
        if not entries:
            return covered
        require(packet['kind'] in ('image_prompt', 'video_prompt') and isinstance(entries, list) and 0 < len(entries) <= 9,
                '已有素材复用仅用于图片或视频提示词，最多9项', 400)
        assets = read_json(self.root / 'assets.json')
        require(isinstance(assets, list), '素材清单格式错误')
        files = {f['path']: f for f in content['files']}
        for entry in entries:
            require(isinstance(entry, dict) and set(entry) == {'path', 'sha256', 'authorization', 'authorizationSha256'},
                    '已有素材复用声明字段无效', 400)
            inside(self.root, entry['path'], {'.png', '.jpg', '.jpeg', '.webp'})
            receipt_path = inside(self.root, entry['authorization'], {'.txt'})
            require(all(isinstance(entry[k], str) and re.fullmatch('[a-f0-9]{64}', entry[k])
                        for k in ('sha256', 'authorizationSha256')), '复用必须绑定素材与原授权依据SHA256', 400)
            require(entry['path'] not in covered and content['request']['referenceHashes'].get(entry['path']) == entry['sha256'],
                    '复用素材未唯一绑定实际请求')
            require(files.get(entry['path'], {}).get('sha256') == entry['sha256'], '复用素材已变化')
            require(files.get(entry['authorization'], {}).get('sha256') == entry['authorizationSha256'], '复用依据已变化')
            receipt = read_json(receipt_path)
            require(isinstance(receipt.get('user_basis'), str) and receipt['user_basis'].strip()
                    and receipt.get('local_image') == entry['path'] and receipt.get('sha256') == entry['sha256'],
                    '复用依据缺少用户原话或素材身份不一致')
            require(any(a.get('id') == receipt.get('reused_asset') and a.get('status') == 'approved'
                        and a.get('image') == entry['path'] for a in assets), '复用素材尚未采用或清单身份不一致')
            covered.add(entry['path'])
        return covered

    def _frame_coverage(self, packet, content, ancestors):
        """An exact PNG frame inherits its approved source, never a new image edit.

        The new video prompt still needs its own user decision. Decode the declared
        frame with the pinned local FFmpeg; labels or director assertions alone do
        not establish provenance. No review journal or source media is modified.
        """
        covered, problems = set(), []
        source_files = {f['path']: f for item in ancestors
                        if item['kind'] == 'video' and item['status'] == 'approved' and item['canApprove']
                        for f in item['files'] if f['exists']}
        actual = {f['path']: f for f in content['files']}
        for entry in packet.get('derivedReferences', []):
            label = entry['path']
            try:
                source = source_files.get(entry['source'])
                require(source and source['sha256'] == entry['sourceSha256'],
                        '原样截帧的源视频未在已通过的上游结果中，或源文件已变')
                require(label in actual and actual[label]['exists'], '原样截帧文件缺失')
                from authorized_cli import runtime_tools
                runtime = runtime_tools(self.root)
                program = self._checked_ffmpeg(runtime)
                key = (source['sha256'], entry['frameIndex'], actual[label]['sha256'], runtime['ffmpeg']['sha256'])
                if key not in self._frame_cache:
                    command = [program, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                               '-i', str(inside(self.root, entry['source'])),
                               '-vf', 'select=eq(n\\,' + str(entry['frameIndex']) + ')',
                               '-fps_mode', 'passthrough', '-frames:v', '1', '-c:v', 'png',
                               '-compression_level', '6', '-f', 'image2pipe', 'pipe:1']
                    result = subprocess.run(command, capture_output=True, timeout=20, check=False)
                    require(result.returncode == 0 and result.stdout.startswith(b'\x89PNG\r\n\x1a\n'),
                            '无法从已通过视频解码指定帧')
                    require(hashlib.sha256(result.stdout).hexdigest() == actual[label]['sha256'],
                            '参考图片不是已通过视频的指定原样帧；改图必须按新素材审核')
                    if len(self._frame_cache) >= 128:
                        self._frame_cache.clear()
                    self._frame_cache[key] = True
                covered.add(label)
            except (ReviewError, OSError, ValueError, KeyError, subprocess.TimeoutExpired, SystemExit) as error:
                problems.append(f'原样截帧校验失败：{label}（{error}）')
        return covered, problems

    def _edit_coverage(self, packet, content, packets):
        """Bind a repair input to an exact earlier result, without approving it.

        This is only for editing the same asset. The replacement prompt and its
        generated result still need their own decisions; other references retain
        the normal upstream review requirements.
        """
        entry = packet.get('editSource')
        if not entry:
            return set()
        source = packets.get(entry['reviewId'])
        require(source and digest(source) == entry['recordSha256'], '返工原图的送审版本已变化')
        require(source['packet']['kind'] == 'image' and source['packet']['subject'] == packet['subject'],
                '返工原图必须是同一素材的历史图片结果')
        bound = next((f for f in source['content']['files'] if f['path'] == entry['path']), None)
        require(bound and bound.get('sha256') == entry['sha256'], '返工原图与历史结果不一致')
        request = json.loads(self._read_cached(inside(self.root, packet['request']), 200_000))
        require(request.get('endpoint') in ('/images/edits', '/v1/images/edits'), '返工原图只允许用于图片编辑请求')
        require(content['request']['referenceHashes'].get(entry['path']) == entry['sha256'], '返工原图未绑定实际请求参考')
        actual = next((f for f in content['files'] if f['path'] == entry['path']), None)
        require(actual and actual.get('sha256') == entry['sha256'], '返工原图文件已变化')
        return {entry['path']}

    def submit(self, packet):
        require(isinstance(packet, dict) and set(packet) <= {'id', 'kind', 'title', 'subject', 'summary', 'files',
                                                           'dependsOn', 'request', 'generation', 'derivedReferences', 'reusedReferences', 'editSource', 'storyboard', 'draft'}, '送审字段不支持', 400)
        packet = json.loads(canonical(packet))
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}', packet.get('id', '')), '审核ID须为稳定的英文编号', 400)
        require(packet.get('kind') in KINDS, '审核类型不支持', 400)
        for key, limit in (('title', 180), ('subject', 120), ('summary', 4000)):
            packet[key] = text(packet.get(key, ''), limit, key, key == 'title')
        require(isinstance(packet.get('files'), list) and len(packet['files']) <= 30, '送审文件格式或数量异常', 400)
        for item in packet['files']:
            require(isinstance(item, dict) and set(item) <= {'path', 'label'} and 'path' in item, '送审文件字段异常', 400)
            inside(self.root, item['path'], TEXT | MEDIA)
            item['label'] = text(item.get('label', item['path']), 180, '文件名称', True)
        deps = packet.setdefault('dependsOn', [])
        require(isinstance(deps, list) and len(deps) <= 100 and all(isinstance(x, str) for x in deps)
                and len(set(deps)) == len(deps) and packet['id'] not in deps, '审核依赖无效', 400)
        if 'draft' in packet:
            draft = packet['draft']
            require(packet['kind'] == 'video_prompt' and isinstance(draft, dict)
                    and {'prompt', 'pending'} <= set(draft) <= {'prompt', 'pending', 'references'},
                    '草稿仅支持视频提示词、待补事项与引用清单', 400)
            inside(self.root, draft['prompt'], TEXT)
            require(isinstance(draft['pending'], list) and 0 < len(draft['pending']) <= 20,
                    '草稿须明确待补素材或正式准备步骤', 400)
            for pending in draft['pending']:
                text(pending, 600, '草稿待补事项', True)
            refs = draft.get('references', [])
            require(isinstance(refs, list) and len(refs) <= 20, '草稿引用清单最多20项', 400)
            ref_ids = set()
            for ref in refs:
                require(isinstance(ref, dict)
                        and {'id', 'kind', 'label', 'status', 'source', 'note'} <= set(ref)
                        <= {'id', 'kind', 'label', 'status', 'source', 'note', 'path', 'sourceClip'},
                        '草稿引用字段无效', 400)
                for key, limit in (('id', 120), ('label', 180), ('source', 240), ('note', 400)):
                    text(ref[key], limit, '引用' + key, True)
                require(ref['id'] not in ref_ids, '草稿引用编号重复', 400)
                ref_ids.add(ref['id'])
                require(ref['kind'] in ('image', 'tail', 'audio')
                        and ref['status'] in ('available', 'candidate', 'pending'), '草稿引用类型或状态无效', 400)
                if ref.get('sourceClip'):
                    require(isinstance(ref['sourceClip'], str) and re.fullmatch(r'EP\d+/Clip\d+', ref['sourceClip']), '引用来源片段编号无效', 400)
                require((ref['status'] == 'pending') == (not ref.get('path')),
                        '待补引用不能伪造文件；已有引用须绑定实际文件', 400)
                if ref.get('path'):
                    extensions = {'.wav', '.mp3', '.ogg', '.flac'} if ref['kind'] == 'audio' else {'.png', '.jpg', '.jpeg', '.webp'}
                    require(inside(self.root, ref['path'], extensions).is_file(), '草稿引用文件不存在', 400)
            require(not any(k in packet for k in ('request', 'generation', 'derivedReferences', 'reusedReferences', 'editSource')),
                    '草稿不能绑定生成请求、批次或正式素材声明', 400)
        elif packet['kind'] in ('image_prompt', 'video_prompt'):
            expected = 'image' if packet['kind'] == 'image_prompt' else 'h3'
            require(isinstance(packet.get('generation'), dict) and set(packet['generation']) == {'tool', 'batch', 'taskDir'}
                    and packet['generation']['tool'] == expected, '图片/视频提示词必须绑定正式生成请求和批次', 400)
            text(packet['generation']['batch'], 160, '生成批次', True)
            attempt = inside(self.root, packet['generation']['taskDir']).relative_to(self.root)
            require(len(attempt.parts) == 3 and attempt.parts[0] == 'tasks'
                    and re.fullmatch(r'attempt-\d{3}', attempt.parts[-1]), '必须绑定唯一的本地生成attempt目录', 400)
            require(bool(packet.get('request')), '提示词送审缺少正式生成请求', 400)
        else:
            require(not packet.get('request') and not packet.get('generation'), '该审核类型不能绑定生成操作', 400)
        if 'storyboard' in packet:
            require(packet['kind'] == 'video_prompt', '合并分镜只用于最终视频提示词', 400)
            inside(self.root, packet['storyboard'], TEXT)
        content = self._content(packet)
        self._reuse_coverage(packet, content)
        if 'editSource' in packet:
            entry = packet['editSource']
            require(packet['kind'] == 'image_prompt' and isinstance(entry, dict)
                    and set(entry) == {'reviewId', 'recordSha256', 'path', 'sha256'}, '返工原图声明字段无效', 400)
            require(isinstance(entry['reviewId'], str) and entry['reviewId'] != packet['id'], '返工原图审核编号无效', 400)
            inside(self.root, entry['path'], {'.png', '.jpg', '.jpeg', '.webp'})
            require(all(isinstance(entry[k], str) and re.fullmatch('[a-f0-9]{64}', entry[k])
                        for k in ('recordSha256', 'sha256')), '返工原图必须绑定历史记录和图片SHA256', 400)
        if 'derivedReferences' in packet:
            entries = packet['derivedReferences']
            require(packet['kind'] == 'video_prompt' and isinstance(entries, list) and 0 < len(entries) <= 9,
                    '原样截帧声明只用于视频提示词参考，最多9张', 400)
            seen = set()
            for entry in entries:
                require(isinstance(entry, dict) and set(entry) == {'path', 'source', 'sourceSha256', 'frameIndex'},
                        '原样截帧声明字段不完整或有额外处理参数', 400)
                inside(self.root, entry['path'], {'.png'})
                inside(self.root, entry['source'], {'.mp4', '.mov', '.webm'})
                require(entry['path'] in content['request']['referencePaths'] and entry['path'] not in seen,
                        '原样截帧须对应唯一实际请求参考图片', 400)
                require(type(entry['frameIndex']) is int and 0 <= entry['frameIndex'] < 10000,
                        '原样截帧序号无效', 400)
                require(isinstance(entry['sourceSha256'], str) and re.fullmatch('[a-f0-9]{64}', entry['sourceSha256']),
                        '原样截帧须绑定实际源视频SHA256', 400)
                seen.add(entry['path'])
        require(content['files'] and all(f['exists'] for f in content['files']), '送审素材缺失，不接受空白或占位送审', 400)
        with self._write_lock():
            records = self.records()
            self._edit_coverage(packet, content, {r['packet']['id']: r for r in records if r['type'] == 'packet'})
            latest = {r['packet']['id']: r['packet'] for r in records if r['type'] == 'packet'}
            require(all(dep in latest for dep in deps), '请先提交上游送审材料', 400)
            latest[packet['id']] = packet
            self._check_graph(latest)
            old = next((r for r in reversed(records) if r['type'] == 'packet' and r['packet']['id'] == packet['id']), None)
            if old and old['packet'] == packet and old['content'] == content:
                return old
            return self._append(records, 'packet', {'packet': packet, 'content': content, 'source': 'DIRECTOR_SUBMISSION'})

    def _check_graph(self, packets):
        require(len(packets) <= 500, '送审项目超过500项，需扩展后继续')
        memo = {}
        def walk(key, stack):
            require(key in packets and key not in stack and len(stack) < 100, '审核依赖缺失、过深或出现循环', 400)
            if key in memo:
                return memo[key]
            found = set()
            for dep in packets[key]['dependsOn']:
                found.add(packets[dep]['kind'])
                found.update(walk(dep, stack | {key}))
            required = REQUIRED.get(packets[key]['kind'], set())
            # The director's internal storyboard travels with the exact video
            # prompt and shares its decision, instead of adding a second gate.
            if packets[key]['kind'] == 'video_prompt' and packets[key].get('storyboard'):
                required = required - {'storyboard'}
            require(required <= found, '缺少本阶段必要的上游审核项', 400)
            memo[key] = found
            return found
        for key in packets:
            walk(key, set())

    def snapshot(self, records=None):
        records = self.records() if records is None else records
        packets = {r['packet']['id']: r for r in records if r['type'] == 'packet'}
        self._check_graph({key: r['packet'] for key, r in packets.items()})
        results, history = {}, []
        for r in records:
            if r['type'] == 'decision':
                history.extend({**entry, 'time': r['time'], 'record': digest(r), 'action': r['action'],
                                'note': r['note'], 'requestId': r['requestId'], 'source': r.get('source', 'CANVAS_USER_ACTION'),
                                **({'delegation': r['delegation']} if 'delegation' in r else {})} for entry in r['entries'])

        def build(key):
            if key in results:
                return results[key]
            record = packets[key]
            packet = record['packet']
            dependencies = [build(dep) for dep in packet['dependsOn']]
            content = self._content(packet)
            fingerprint = digest({'packet': digest(record), 'content': content,
                                  'dependencies': {d['id']: d['fingerprint'] for d in dependencies}})
            previous = [r for r in history if r['id'] == key]
            decisions = [r for r in previous if r['action'] != 'comment']
            last = decisions[-1] if decisions else None
            status = ('stale' if last['fingerprint'] != fingerprint else
                      {'approve': 'approved', 'request_changes': 'changes_requested', 'defer': 'deferred',
                       'resume': 'pending'}[last['action']]) if last else 'pending'
            problems = [f'上游尚未确认：{d["title"]}' for d in dependencies if d['status'] != 'approved']
            if packet.get('draft'):
                problems += ['草稿尚未形成正式请求，不能批准生成：' + item for item in packet['draft']['pending']]
                if not last or last['action'] == 'resume':
                    status = 'draft'
            problems += [f'文件缺失：{f["label"]}' for f in content['files'] if not f['exists']]
            if content['request']:
                problems.extend(content['request']['warnings'])
                actual_hashes = {f['path']: f['sha256'] for f in content['files']}
                problems += [f'参考素材与正式请求不一致，请重新准备请求：{p}'
                             for p, sha in content['request']['referenceHashes'].items() if actual_hashes.get(p) != sha]
                # Every generated reference must belong to an approved upstream
                # media packet, not merely be a file attached to this prompt.
                ancestors = set()
                def collect(dep):
                    if dep in ancestors:
                        return
                    ancestors.add(dep)
                    for parent in packets[dep]['packet']['dependsOn']:
                        collect(parent)
                for dep in packet['dependsOn']:
                    collect(dep)
                covered = {f['path'] for dep in ancestors if packets[dep]['packet']['kind'] in ('image', 'video', 'audio')
                           for f in build(dep)['files'] if f['exists']}
                derived, errors = self._frame_coverage(packet, content, [build(dep) for dep in ancestors])
                covered.update(derived)
                problems.extend(errors)
                try:
                    covered.update(self._reuse_coverage(packet, content))
                except (ReviewError, OSError, ValueError, KeyError) as error:
                    problems.append(f'已有素材复用校验失败：{error}')
                try:
                    covered.update(self._edit_coverage(packet, content, packets))
                except (ReviewError, OSError, ValueError, KeyError) as error:
                    problems.append(f'返工原图校验失败：{error}')
                problems += [f'参考素材缺少上游结果审核：{p}' for p in content['request']['referencePaths'] if p not in covered]
            if problems and status == 'approved':
                status = 'blocked'
            result = {**packet, **content, 'fingerprint': fingerprint, 'status': status,
                      'stage': KINDS[packet['kind']][1], 'kindLabel': KINDS[packet['kind']][0],
                      'head': previous[-1]['record'] if previous else None,
                      'canApprove': not problems, 'canResume': bool(last and last['action'] == 'defer'), 'problems': problems,
                      'history': previous[-100:], 'submittedAt': record['time']}
            results[key] = result
            return result
        for key in packets:
            build(key)
        return {'items': sorted(results.values(), key=lambda x: (x['stage'], x['id'])),
                'history': history[-300:][::-1], 'recordCount': len(records),
                'generationTriggered': False, 'storage': BASE + '/记录'}

    def decide_delegated(self, authorization, assessment):
        """Record an actual delegated review, never impersonate a canvas user."""
        auth_path = inside(self.root, authorization, {'.json'})
        evidence_path = inside(self.root, assessment, {'.json'})
        auth, evidence = read_json(auth_path), read_json(evidence_path)
        from codex_workflow import Work
        work = Work(self.root).spec
        require(auth.get('status') == 'USER_APPROVED_CODEX_MEDIA_EXECUTION' and
                auth.get('approved') is True and auth.get('delegatedReview') is True and
                auth.get('workId') == work.get('workId') and
                auth.get('projectRoot') == str(self.root) and
                bool(auth.get('userQuote', '').strip()), '缺少本作品真实用户委托审核授权')
        require(evidence.get('reviewer') == '总导演' and
                evidence.get('action') in ('approve', 'request_changes') and
                isinstance(evidence.get('checks'), list) and evidence['checks'] and
                len(evidence.get('note', '').strip()) >= 12, '委托审核缺少具体检查记录')
        bound = {}
        for row in evidence['checks']:
            require(row.get('result') in ('pass', 'fail', 'limitation') and
                    len(row.get('note', '').strip()) >= 8 and row.get('evidence'), '委托审核检查或依据缺失')
            require(evidence['action'] != 'approve' or row['result'] == 'pass', '未完成或未通过的检查不能采用')
            for ref in row['evidence']:
                path = inside(self.root, ref['path'])
                require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == ref['sha256'],
                        '委托审核依据已改变或缺失')
                bound[ref['path']] = ref['sha256']
        payload = {'requestId': evidence['requestId'], 'action': evidence['action'],
                   'note': '【用户授权·总导演代审】' + evidence['note'],
                   'items': [{k: evidence[k] for k in ('id', 'fingerprint', 'head')}]}
        delegation = {'authorization': authorization, 'authorizationSha256': hashlib.sha256(auth_path.read_bytes()).hexdigest(),
                      'assessment': assessment, 'assessmentSha256': hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                      'reviewer': '总导演', 'evidence': bound}
        return self.decide(payload, _delegation=delegation)

    def decide(self, payload, *, _delegation=None):
        require(isinstance(payload, dict) and set(payload) == {'requestId', 'action', 'note', 'items'}, '审核操作字段无效', 400)
        action = payload['action']
        require(action in ACTIONS, '不支持的审核操作', 400)
        text(payload['requestId'], 100, '请求编号', True)
        note = text(payload['note'], 4000, '修改意见')
        require(action not in ('request_changes', 'comment') or bool(note), '请填写具体修改意见或补充内容', 400)
        entries = payload['items']
        require(isinstance(entries, list) and 1 <= len(entries) <= 100, '请选择1至100项审核内容', 400)
        require(all(isinstance(e, dict) and set(e) == {'id', 'fingerprint', 'head'}
                    and isinstance(e['id'], str) for e in entries), '审核版本字段异常', 400)
        require(len({e['id'] for e in entries}) == len(entries), '审核内容重复', 400)
        with self._write_lock():
            records = self.records()
            old = [r for r in records if r['type'] == 'decision' and r['requestId'] == payload['requestId']]
            if old:
                require(old[0]['payloadSha256'] == digest(payload), '重复请求编号对应不同内容')
                return {'saved': True, 'duplicate': True, 'record': digest(old[0]), 'generationTriggered': False}
            current = {item['id']: item for item in self.snapshot(records)['items']}
            saved = []
            for entry in entries:
                item = current.get(entry['id'])
                require(item and item['fingerprint'] == entry['fingerprint'] and item['head'] == entry['head'],
                        '内容或审核记录已经变化，本批未保存；请刷新后重新审核')
                require(action != 'approve' or item['canApprove'], '上游未通过、素材缺失或请求有警告，本批不能批准')
                require(action != 'resume' or item['canResume'], '只有当前仍有暂缓决定的项目才能取消暂缓；本批未保存')
                saved.append({'id': item['id'], 'title': item['title'], 'kind': item['kind'],
                              'fingerprint': item['fingerprint'], 'content': {'files': item['files'], 'request': item['request']}})
            record = self._append(records, 'decision', {'requestId': payload['requestId'], 'action': action,
                                  'note': note, 'entries': saved, 'payloadSha256': digest(payload),
                                  'source': 'DELEGATED_DIRECTOR_REVIEW' if _delegation else 'CANVAS_USER_ACTION',
                                  **({'delegation': _delegation} if _delegation else {})})
            return {'saved': True, 'duplicate': False, 'record': digest(record), 'generationTriggered': False}

    def require_generation(self, tool, request_path, batch, task_dir):
        path = Path(request_path).resolve()
        require(path.is_relative_to(self.root), '生成请求不属于当前作品')
        relative_path = path.relative_to(self.root).as_posix()
        matches = [item for item in self.snapshot()['items'] if item.get('request')
                   and item['request']['path'] == relative_path and item.get('generation') ==
                   {'tool': tool, 'batch': batch, 'taskDir': relative(self.root, str(task_dir))}]
        require(len(matches) == 1 and matches[0]['status'] == 'approved' and matches[0]['canApprove'],
                '正式生成请求尚未获得用户审核或明确委托审核，或确认版本已失效')
        return matches[0]['fingerprint']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    submit = sub.add_parser('submit', help='总导演提交送审包；不能批准')
    submit.add_argument('--packet', type=Path, required=True)
    sub.add_parser('status', help='读取当前审核结果和历史；不执行制作')
    delegated = sub.add_parser('delegate', help='仅凭本作品明确用户委托及具体证据记录总导演代审')
    delegated.add_argument('--authorization', required=True)
    delegated.add_argument('--assessment', required=True)
    args = parser.parse_args()
    store = ReviewStore(args.project)
    result = (store.submit(read_json(args.packet)) if args.command == 'submit' else
              store.decide_delegated(args.authorization, args.assessment) if args.command == 'delegate' else store.snapshot())
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except (ReviewError, OSError, ValueError) as error:
        raise SystemExit(str(error))
