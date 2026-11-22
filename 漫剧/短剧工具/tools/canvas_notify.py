"""Batched canvas-review outbox. Local files only; never calls a model or CLI agent.

The active trusted Codex controller supplies real app-tool observations. This
module cannot send to a chat on its own, and an outbox item is not a delivery.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canvas_review import ReviewStore, ReviewError, require, canonical, digest, inside, read_json, text

ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z')
LABELS = {'queued': '待投递', 'dispatching': '投递中', 'delivered': '已投递', 'received': '总导演已接收',
          'delivery_unknown': '投递结果待核实', 'blocked': '内容变化 · 待重新审核'}


def sealed_read(path):
    value = read_json(path)
    require(isinstance(value, dict), '会话登记格式异常')
    body = {k: v for k, v in value.items() if k != 'recordSha256'}
    require(value.get('recordSha256') == digest(body), '会话登记校验失败')
    return body


def app_body(response):
    """Only unwrap an actual tool result; do not infer success from prose."""
    require(isinstance(response, dict) and response.get('isError') is not True, '会话工具没有确认成功')
    if isinstance(response.get('structuredContent'), dict):
        return response['structuredContent']
    if response.get('schemaVersion') == 1:
        return response
    candidates = []
    for block in response.get('content', []):
        if block.get('type') == 'text':
            try:
                value = json.loads(block['text'])
                if isinstance(value, dict):
                    candidates.append(value)
            except (ValueError, KeyError):
                pass
    require(len(candidates) == 1, '会话工具返回未能核实，不能当作已投递')
    return candidates[0]


class NotificationStore(ReviewStore):
    RECORD_TYPES = ('batch', 'claim', 'delivery', 'receipt')

    def __init__(self, root):
        super().__init__(root)
        self.journal = self.base/'通知记录'
        self.reviews = ReviewStore(root)
        self.listener = self.base/'通知监听状态.json'

    def route(self):
        try:
            spec = sealed_read(inside(self.root, '协作/工作区.json'))
            binding = sealed_read(inside(self.root, '协作/会话.json'))
            role = binding.get('roles', {}).get('总导演', {})
            require(spec.get('version') == 1 and spec.get('root') == str(self.root)
                    and ID.fullmatch(spec.get('workId', '')) and binding.get('workId') == spec.get('workId')
                    and type(spec.get('simulation')) is bool,
                    '作品与会话登记不匹配')
            require(role.get('source') == 'CODEX_APP_CREATE_THREAD' and role.get('status') == 'ready'
                    and role.get('simulation') is spec['simulation'] and ID.fullmatch(role.get('threadId', ''))
                    and ID.fullmatch(role.get('hostId', '')), '本片总导演尚未完成会话关联')
            value = {'workId': spec['workId'], 'threadId': role['threadId'], 'hostId': role['hostId'],
                     'simulation': spec['simulation']}
            return {**value, 'available': True, 'fingerprint': digest(value)}
        except (ValueError, OSError, KeyError, TypeError):
            return {'available': False, 'reason': '本片总导演尚未关联，或会话登记校验失败', 'fingerprint': None}

    def listener_status(self):
        if not self.listener.exists():
            return {'online': False, 'label': '调度未在线等待，通知将保留在队列'}
        try:
            value = read_json(inside(self.root, self.listener.relative_to(self.root).as_posix(), {'.json'}))
            online = (isinstance(value, dict) and type(value.get('expiresAt')) in (int, float)
                      and value['expiresAt'] > time.time() and value.get('mode') == 'review-only')
            return {'online': online, 'label': '调度正在等待审核通知' if online else '调度未在线等待，通知将保留在队列'}
        except (ValueError, OSError, TypeError):
            return {'online': False, 'label': '调度监听状态待核对，通知仍保留'}

    def heartbeat(self, owner, active=True):
        require(isinstance(owner, str) and ID.fullmatch(owner), '调度会话标识无效', 400)
        with self._write_lock():
            inside(self.root, self.listener.relative_to(self.root).as_posix(), {'.json'})
            if self.listener.is_file():
                old = read_json(self.listener)
                require(old.get('owner') == owner or old.get('expiresAt', 0) < time.time(), '已有其他调度在等待，未抢占')
            value = {'schemaVersion': 1, 'owner': owner, 'mode': 'review-only', 'pid': os.getpid(),
                     'expiresAt': time.time()+90 if active else 0}
            temporary = self.base/f'.{uuid.uuid4().hex}.tmp'
            try:
                with temporary.open('xb') as stream:
                    stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
                os.replace(temporary, self.listener)
            finally:
                temporary.unlink(missing_ok=True)

    def _batches(self, records):
        batches = {}
        for record in records:
            if record['type'] == 'batch':
                batches[record['id']] = {**record, 'state': 'queued', 'claimId': None, 'turnId': None}
            else:
                require(record.get('notificationId') in batches, '通知记录引用缺失，已停止投递')
                batch = batches[record['notificationId']]
                if record['type'] == 'claim':
                    require(batch['state'] == 'queued', '通知存在重复领取，已停止投递')
                    batch.update(state='dispatching', claimId=record['claimId'], owner=record['owner'], claimedAt=record['time'])
                elif record['type'] == 'delivery':
                    require(batch['state'] == 'dispatching' and batch['claimId'] == record['claimId'], '通知投递状态异常')
                    batch.update(state=record['outcome'], turnId=record.get('turnId'), detail=record.get('detail', ''))
                elif record['type'] == 'receipt':
                    require(batch['state'] in ('dispatching', 'delivered', 'delivery_unknown'), '通知回执状态异常')
                    batch.update(state='received', turnId=record['turnId'], receivedAt=record['time'], finalSha256=record['finalSha256'])
        return batches

    def snapshot(self, records=None, *, review_records=None, review_snapshot=None):
        # The canvas can reuse the exact records and projection it just read.
        # Write/dispatch callers omit these and still validate current state.
        require((review_records is None) == (review_snapshot is None), '审核快照与记录必须成对提供')
        records = self.records() if records is None else records
        batches = self._batches(records)
        cutoff = max((b['throughSeq'] for b in batches.values() if b['state'] != 'blocked'), default=0)
        review_records = self.reviews.records() if review_records is None else review_records
        pending = [r for r in review_records if r['type'] == 'decision' and r['seq'] > cutoff]
        route = self.route()
        pending_hashes = [digest(r) for r in pending]
        fingerprint = digest({'records': pending_hashes, 'route': route['fingerprint']})
        review_snapshot = self.reviews.snapshot(review_records) if review_snapshot is None else review_snapshot
        items = {i['id']: i for i in review_snapshot['items']}
        latest = {entry['id']: entry for record in pending for entry in record['entries']}
        stale = any(entry['fingerprint'] != items.get(key, {}).get('fingerprint') for key, entry in latest.items())
        queue = []
        for batch in reversed(list(batches.values())):
            state = batch['state']
            if state == 'dispatching' and time.time()-datetime.fromisoformat(batch['claimedAt']).timestamp() > 90:
                state = 'delivery_unknown'
            queue.append({'id': batch['id'], 'state': state, 'label': LABELS[state], 'time': batch['time'],
                          'decisionCount': len(batch['decisionRecords']), 'itemCount': len(batch['versions']),
                          'receivedAt': batch.get('receivedAt'), 'detail': batch.get('detail', '')})
        return {'route': route, 'listener': self.listener_status(), 'unnotifiedDecisions': len(pending),
                'unnotifiedItems': len(latest), 'batchFingerprint': fingerprint,
                'canNotify': bool(pending) and route['available'] and not stale,
                'blockedReason': '本批内容已变化，请重新审核后通知' if stale else route.get('reason', ''),
                'queue': queue[:100], 'generationTriggered': False}

    def enqueue(self, payload):
        require(isinstance(payload, dict) and set(payload) == {'requestId', 'batchFingerprint'}, '通知提交字段无效', 400)
        text(payload['requestId'], 100, '通知请求编号', True)
        with self._write_lock():
            records = self.records()
            old = next((r for r in records if r['type'] == 'batch' and r['requestId'] == payload['requestId']), None)
            if old:
                require(old['payloadSha256'] == digest(payload), '通知请求编号对应不同批次')
                return {'queued': True, 'duplicate': True, 'notificationId': old['id'], 'generationTriggered': False}
            current = self.snapshot(records)
            require(current['batchFingerprint'] == payload['batchFingerprint'], '本批审核或导演关联已变化，请刷新后再通知')
            require(current['canNotify'], current['blockedReason'] or '没有尚未通知的已保存审核')
            cutoff = max((b['throughSeq'] for b in self._batches(records).values() if b['state'] != 'blocked'), default=0)
            decisions = [r for r in self.reviews.records() if r['type'] == 'decision' and r['seq'] > cutoff]
            ids = {entry['id'] for r in decisions for entry in r['entries']}
            items = {i['id']: i for i in self.reviews.snapshot()['items']}
            identifier = uuid.uuid4().hex
            record = self._append(records, 'batch', {'id': identifier, 'requestId': payload['requestId'],
                'payloadSha256': digest(payload), 'batchFingerprint': payload['batchFingerprint'], 'route': current['route'],
                'throughSeq': decisions[-1]['seq'], 'decisionRecords': [{'seq': r['seq'], 'sha256': digest(r)} for r in decisions],
                'versions': [{k: items[id][k] for k in ('id', 'fingerprint', 'head')} for id in sorted(ids)],
                'mode': 'review-only', 'source': 'CANVAS_USER_BATCH_NOTIFICATION'})
            return {'queued': True, 'duplicate': False, 'notificationId': record['id'], 'generationTriggered': False}

    def _unchanged(self, batch):
        route = self.route()
        if not route['available'] or route['fingerprint'] != batch['route']['fingerprint']:
            return False
        items = {i['id']: i for i in self.reviews.snapshot()['items']}
        return all(all(items.get(v['id'], {}).get(k) == v[k] for k in ('fingerprint', 'head')) for v in batch['versions'])

    def claim(self, owner):
        with self._write_lock():
            records = self.records()
            batches = self._batches(records)
            batch = next((b for b in batches.values() if b['state'] == 'queued'), None)
            if batch is None:
                return {'state': 'idle'}
            claim = uuid.uuid4().hex
            record = self._append(records, 'claim', {'notificationId': batch['id'], 'claimId': claim, 'owner': owner})
            if not self._unchanged(batch):
                self._append([*records, record], 'delivery', {'notificationId': batch['id'], 'claimId': claim,
                    'outcome': 'blocked', 'detail': '送出前内容或导演关联已变化，本批未发消息', 'turnId': None})
                return {'state': 'blocked', 'notificationId': batch['id']}
            prompt = (f'本片画布审核已提交一批。通知ID：{batch["id"]}\n作品ID：{batch["route"]["workId"]}\n'
                      f'唯一作品根：{self.root}\n请读取该作品 协作/审核/通知记录 中本通知及其引用的审核记录。'
                      '用户已在画布保存决定和意见，并点击一次本批通知按钮；以文件里的实际版本为准，不能把通知当作新增制作授权。\n'
                      '当前制作保持暂停：只核对本批审核并回报接收，不向四岗派活，不生成、不配音、不重试，不调用其他模型或外部接口。\n'
                      '最后返回单个JSON：'
                      + json.dumps({'schemaVersion': 1, 'notificationId': batch['id'], 'workId': batch['route']['workId'],
                          'batchFingerprint': batch['batchFingerprint'], 'status': 'received',
                          'reviewRecordsDigest': digest(batch['decisionRecords']),
                          'summary': '填写已读取的决定及意见摘要；不是制作完成'}, ensure_ascii=False))
            return {'state': 'claimed', 'notificationId': batch['id'], 'claimId': claim,
                    'route': batch['route'], 'prompt': prompt, 'batchFingerprint': batch['batchFingerprint']}

    def wait(self, owner, timeout=30):
        require(type(timeout) is int and 0 <= timeout <= 50, '本地等待须在0至50秒内', 400)
        self.heartbeat(owner)
        end = time.monotonic()+timeout
        while True:
            result = self.claim(owner)
            if result['state'] != 'idle' or time.monotonic() >= end:
                return result
            time.sleep(min(.3, max(0, end-time.monotonic())))

    def awaiting_receipts(self):
        return [{'notificationId': b['id'], 'route': b['route'], 'turnId': b.get('turnId')}
                for b in self._batches(self.records()).values()
                if b['state'] in ('dispatching', 'delivered', 'delivery_unknown')]

    def delivery(self, notification_id, claim_id, response):
        with self._write_lock():
            records = self.records(); batch = self._batches(records).get(notification_id)
            require(batch and batch['state'] == 'dispatching' and batch['claimId'] == claim_id, '投递记录不属于当前领取')
            outcome, turn_id = 'delivery_unknown', None
            try:
                value = app_body(response)
                thread_id = value.get('threadId') or value.get('thread', {}).get('id')
                host_id = value.get('hostId') or value.get('thread', {}).get('hostId')
                turn_id = value.get('turnId') or value.get('turn', {}).get('id')
                status = value.get('status') or value.get('turn', {}).get('status')
                require(not value.get('error') and thread_id == batch['route']['threadId']
                        and host_id in (None, batch['route']['hostId']) and isinstance(turn_id, str) and ID.fullmatch(turn_id)
                        and status in ('queued', 'accepted', 'sent', 'inProgress', 'running'), '未能核实会话受理身份')
                outcome = 'delivered'
            except (ReviewError, ValueError, TypeError):
                turn_id = None
            self._append(records, 'delivery', {'notificationId': notification_id, 'claimId': claim_id,
                'outcome': outcome, 'turnId': turn_id, 'sourceTool': 'codex-app-tools.send_message_to_thread',
                'observationSha256': digest(response), 'detail': '' if outcome == 'delivered' else '不自动重发；等待原会话回执或人工核对'})
            return {'state': outcome, 'notificationId': notification_id, 'turnId': turn_id}

    def receipt(self, notification_id, response):
        value = app_body(response)
        with self._write_lock():
            records = self.records(); batch = self._batches(records).get(notification_id)
            require(batch and batch['state'] != 'queued', '通知尚未投递')
            thread = value.get('thread', {})
            require(type(value.get('schemaVersion')) is int and value['schemaVersion'] == 1 and thread.get('kind') == 'codex'
                    and thread.get('id') == batch['route']['threadId'] and thread.get('hostId') == batch['route']['hostId'],
                    '接收回执不属于本片总导演')
            found = []
            for turn in value.get('turns', []):
                if turn.get('status') != 'completed' or turn.get('error') is not None:
                    continue
                if batch.get('turnId') and turn.get('id') != batch['turnId']:
                    continue
                finals = [i for i in turn.get('items', []) if i.get('type') == 'agentMessage' and i.get('phase') == 'final_answer']
                if len(finals) != 1 or finals[0].get('truncated') or not isinstance(finals[0].get('text'), str):
                    continue
                try:
                    ack = json.loads(finals[0]['text'])
                    if (type(ack.get('schemaVersion')) is int and ack['schemaVersion'] == 1 and ack.get('notificationId') == notification_id
                            and ack.get('workId') == batch['route']['workId'] and ack.get('status') == 'received'
                            and ack.get('batchFingerprint') == batch['batchFingerprint']
                            and ack.get('reviewRecordsDigest') == digest(batch['decisionRecords'])):
                        found.append((turn['id'], digest(finals[0]['text'])))
                except (ValueError, TypeError, AttributeError):
                    pass
            require(len(found) == 1, '尚未取得唯一、完整且匹配本批的总导演接收回执')
            turn_id, final_sha = found[0]
            if batch['state'] == 'received':
                require(batch['turnId'] == turn_id and batch['finalSha256'] == final_sha, '既有回执不一致')
                return {'state': 'received', 'duplicate': True}
            require(batch['state'] in ('dispatching', 'delivered', 'delivery_unknown'), '通知状态不允许记录接收')
            self._append(records, 'receipt', {'notificationId': notification_id, 'turnId': turn_id,
                'finalSha256': final_sha, 'sourceTool': 'codex-app-tools.read_thread', 'observationSha256': digest(response)})
            return {'state': 'received', 'duplicate': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    sub.add_parser('pending-receipts')
    wait = sub.add_parser('wait'); wait.add_argument('--owner', required=True); wait.add_argument('--timeout', type=int, default=30)
    stop = sub.add_parser('stop'); stop.add_argument('--owner', required=True)
    delivery = sub.add_parser('delivery'); delivery.add_argument('--notification', required=True); delivery.add_argument('--claim', required=True)
    delivery.add_argument('--observation', required=True)
    receipt = sub.add_parser('receipt'); receipt.add_argument('--notification', required=True); receipt.add_argument('--observation', required=True)
    args = parser.parse_args(); store = NotificationStore(args.project)
    if args.command == 'wait': result = store.wait(args.owner, args.timeout)
    elif args.command == 'pending-receipts': result = {'items': store.awaiting_receipts()}
    elif args.command == 'stop': store.heartbeat(args.owner, False); result = {'online': False}
    elif args.command in ('delivery', 'receipt'):
        require(args.observation.startswith('协作/审核/投递证据/'), '会话工具证据只允许读取本作品专用目录', 400)
        observation = read_json(inside(store.root, args.observation, {'.json'}))
        result = store.delivery(args.notification, args.claim, observation) if args.command == 'delivery' else store.receipt(args.notification, observation)
    else: result = store.snapshot()
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise SystemExit(str(error))
