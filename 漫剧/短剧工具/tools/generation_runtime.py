"""Shared local generation state, locking and recoverable accounting.

Lock order: attempt -> global admission -> budget -> ledger. Pipeline writes
occur separately, after accounting locks have been released. No network here.
"""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

try:
    import fcntl
except ImportError:
    fcntl = None
    import msvcrt

ACTIVE = {'submitting', 'submitted', 'pending', 'queued', 'generating', 'running', 'unknown'}
SHARED_PROVIDERS = {'minimax-h3', 'compshare-indextts'}


def fail(message):
    raise SystemExit('ERROR: ' + str(message))


@contextmanager
def exclusive_lock(path, timeout=60):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with path.open('a+b') as handle:
        while True:
            try:
                handle.seek(0)
                if fcntl:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    fail(f'lock wait exceeded {timeout}s: {path}')
                time.sleep(.05)
        try:
            yield
        finally:
            handle.seek(0)
            if fcntl:
                fcntl.flock(handle, fcntl.LOCK_UN)
            else:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        deadline = time.monotonic() + 10
        while True:
            try:
                os.replace(name, path)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        fail(f'cannot read state {path}: {type(e).__name__}; refusing to ignore it')


def amount(value):
    try:
        d = Decimal(str(value))
        if not d.is_finite():
            raise ValueError
        return d.quantize(Decimal('.01'))
    except (ValueError, InvalidOperation):
        fail('invalid accounting amount')


def identity(task, digest):
    # Persist this result in state; retries/relocation must use the saved key.
    scope = os.path.normcase(str(Path(task).resolve()))
    return 'nv-' + hashlib.sha256(f'{scope}|{digest}'.encode('utf-8')).hexdigest()


def scan(works, providers=SHARED_PROVIDERS):
    out = []
    for path in sorted(Path(works).glob('*/tasks/*/attempt-*/state.json')):
        s = read_json(path)
        if not isinstance(s, dict) or not s.get('provider') or not s.get('status'):
            fail(f'incomplete task state: {path}')
        # A process may die after budget reservation but before the charge reaches
        # the ledger. Do not admit another task against incomplete daily totals.
        if s.get('accounting_version') == 1:
            journal = path.with_name('accounting-charge.json')
            if not journal.exists() or not read_json(journal).get('done'):
                fail(f'incomplete reservation accounting: {path}; recover the original attempt first')
        if s['provider'] not in providers or s['status'] not in ACTIVE:
            continue
        submitted = s.get('submitted_at', time.time())
        if isinstance(submitted, str):
            try:
                submitted = datetime.fromisoformat(submitted).timestamp()
            except ValueError:
                fail(f'invalid submitted_at: {path}')
        out.append({'project': path.parts[-5], 'task_dir': str(path.parent),
                    'attempt': path.parent.name, 'provider': s['provider'],
                    'status': s['status'], 'task_id': s.get('task_id'),
                    'minutes': round((time.time() - submitted) / 60, 1)})
    # A reservation without state could have been interrupted before/after POST.
    for path in Path(works).glob('*/tasks/*/attempt-*/submission.lock'):
        if not path.with_name('state.json').exists():
            fail(f'reservation without state: {path}; manual reconciliation required')
    return out


def read_cap(root, pool='h3'):
    p = Path(root) / '全局设置.json'
    s = read_json(p) if p.exists() else {'h3': {'max_running_tasks': 3}, 'nexorai': {'max_running': 2}}
    value = s.get(pool, {}).get('max_running_tasks' if pool == 'h3' else 'max_running')
    if type(value) is not int or value < 1:
        fail(f'invalid concurrency cap: {p}')
    return value


def ledger_once(root, entry):
    root = Path(root)
    with exclusive_lock(root / '全局账本.lock'):
        p = root / '全局账本.jsonl'
        text = p.read_text(encoding='utf-8') if p.exists() else ''
        try:
            entries = [json.loads(line) for line in text.splitlines() if line.strip()]
        except ValueError:
            fail(f'ledger damaged: {p}; manual reconciliation required')
        if text and not text.endswith('\n'):
            fail(f'ledger has incomplete tail: {p}; refusing to append')
        for old in entries:
            if old.get('operation_id') == entry['operation_id']:
                if any(old.get(k) != v for k, v in entry.items() if k != 'time'):
                    fail('conflicting accounting operation ID')
                return
        with p.open('a', encoding='utf-8', newline='\n') as f:
            f.write(json.dumps({'time': datetime.now().astimezone().isoformat(), **entry},
                               ensure_ascii=False, allow_nan=False) + '\n')
            f.flush()
            os.fsync(f.fileno())


def validate_budget(budget, batch, kind, delta):
    batches = read_json(budget)
    hits = [b for b in batches if b.get('batch') == batch]
    if len(hits) != 1:
        fail(f'budget batch not found (or duplicated): {batch}')
    b = hits[0]
    if amount(delta) <= 0 or amount(b.get('used', 0)) < 0 or amount(b['limit']) < 0:
        fail('invalid budget or non-positive reservation')
    if kind == 'image' and amount(delta) != int(amount(delta)):
        fail('image reservation must be an integer')
    if b.get('kind') != kind or b.get('unit', 'images' if kind == 'image' else 'points') != ('images' if kind == 'image' else 'points'):
        fail(f'budget batch {batch} is not a {kind} batch（不是生图批次）' if kind == 'image' else f'budget batch {batch} is not a {kind} batch')
    if delta > 0 and b.get('pending_settlements'):
        fail(f'budget {batch} has unsettled actual costs; ask the director')
    if amount(b.get('used', 0)) + amount(delta) > amount(b['limit']):
        fail(f'budget exceeded for {batch}; ask the director for more')
    return batches, b


def accounting(state, task, event, delta, fields, patch):
    """Caller owns attempt lock. Intent + atomic budget receipt + deduped ledger.

    A crash after any write can be recovered without applying delta twice.
    Unrelated batch updates survive because budget is reloaded under its lock.
    """
    task = Path(task)
    ch = state['charge']
    intent_path = task / f'accounting-{event}.json'
    if intent_path.exists():
        intent = read_json(intent_path)
    else:
        budget = Path(ch['file'])
        if event == 'charge':
            with exclusive_lock(str(budget) + '.lock'):
                validate_budget(budget, ch['batch'], ch['kind'], delta)
        op = identity(task, event)
        intent = {'operation_id': op, 'event': event, 'delta': float(amount(delta)),
                  'charge': ch.copy(), 'fields': fields, 'patch': patch, 'done': False}
        atomic_json(intent_path, intent)  # write-ahead; no budget has changed yet
    _apply(intent_path, intent, state)


def _apply(path, intent, state):
    if intent['done']:
        return
    ch = intent['charge']
    budget = Path(ch['file'])
    root = budget.parent.parent.parent.parent
    op = intent['operation_id']
    kind = ch.get('kind', 'speech' if state['provider'] == 'compshare-indextts' else 'video')
    with exclusive_lock(str(budget) + '.lock'):
        batches = read_json(budget)
        hits = [b for b in batches if b.get('batch') == ch['batch']]
        if len(hits) != 1 or hits[0].get('kind') != kind:
            fail('accounting batch missing or changed')
        b = hits[0]
        receipts = b.setdefault('_operations', {})
        delta = amount(intent['delta'])
        if op not in receipts:
            used = amount(b.get('used', 0)) + delta
            if used < 0:
                fail('refund would make budget negative; reconciliation required')
            if used > amount(b['limit']):
                if intent['event'] != 'settle':
                    fail('budget exceeded during recovery')
                b.setdefault('pending_settlements', {})[op] = intent['fields']['actual_points']
                atomic_json(budget, batches)
                state['charge'].update(settlement_pending=True, actual_points=intent['fields']['actual_points'])
                atomic_json(path.parent / 'state.json', state)
                return
            b['used'] = float(used) if kind != 'image' else int(used)
            receipts[op] = float(delta)
            b.get('pending_settlements', {}).pop(op, None)
            atomic_json(budget, batches)
        elif amount(receipts[op]) != delta:
            fail('conflicting budget receipt')
        ledger_once(root, {'operation_id': op, 'event': intent['event'],
                          'provider_event': {'minimax-h3': 'h3', 'compshare-indextts': 'indextts',
                                             'nexorai': 'nexorai'}[state['provider']],
                          **({'provider': 'nexorai'} if kind == 'image' else {}),
                          'project': ch.get('project', path.parents[3].name), 'batch': ch['batch'],
                          'task_dir': str(path.parent), **intent['fields']})
    state['charge'].update(intent['patch'])
    state['charge'].pop('settlement_pending', None)
    if intent['event'] == 'refund':
        state.pop('refund_pending', None)
    atomic_json(path.parent / 'state.json', state)
    intent['done'] = True
    atomic_json(path, intent)


def recover(task):
    task = Path(task)
    for p in sorted(task.glob('accounting-*.json')):
        intent = read_json(p)
        if not intent['done']:
            state = read_json(task / 'state.json')
            _apply(p, intent, state)


def attempt_command(func):
    def command(a):
        if getattr(a, 'execute', True) is False:
            return func(a)
        task = Path(a.task_dir)
        task = task.resolve()
        a.task_dir = task
        if func.__name__=='cmd_submit':
            from execution_limits import check_retry
            check_retry(task,getattr(a,'retry_unknown',False))
        task.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(task / '.attempt.lock'):
            recover(task)
            if func.__name__ == 'cmd_submit' and not getattr(a, 'retry_unknown', False):
                if (task / 'state.json').exists() or (task / 'submission.lock').exists():
                    fail('attempt already reserved（已经提交过）; query it or retry unknown with the saved key')
            return func(a)
    return command


def check_output(root, output):
    """An attempt may create a new output, never replace an existing work."""
    if not output:
        return
    output = Path(output).resolve()
    if not output.is_relative_to(Path(root).resolve()):
        fail('output must remain inside this project')
    if output.exists():
        fail('output already exists; refusing to overwrite it')


def publish_output(temporary, output):
    """Atomic no-clobber delivery on the same filesystem (Windows NTFS supported).

    An existence check followed by os.replace is racy across attempts. link()
    creates a new directory entry atomically and fails if someone won the race.
    """
    os.link(temporary, output)
    Path(temporary).unlink()


def reserve(state, task, kind, budget, batch, estimate, fields):
    task = Path(task)
    with exclusive_lock(str(budget) + '.lock'):
        validate_budget(budget, batch, kind, estimate)
    if (task / 'submission.lock').exists() or (task / 'state.json').exists():
        fail('attempt already reserved; query it or retry the unknown attempt')
    from execution_limits import reserve as reserve_execution
    reserve_execution(task,state,kind,estimate,fields,batch)
    state['task_dir'] = str(task)
    state['accounting_version'] = 1
    state['charge'] = {'file': str(Path(budget).resolve()), 'batch': batch, 'kind': kind,
                       'project': task.parents[2].name, 'refunded': False,
                       'images' if kind == 'image' else 'points': estimate}
    if kind == 'image':
        state['charge']['date'] = fields['date']
    atomic_json(task / 'state.json', state)
    (task / 'submission.lock').write_text(state.get('request_sha256', ''), encoding='utf-8', newline='\n')
    accounting(state, task, 'charge', estimate, fields, {})


def refund(state):
    ch = state.get('charge')
    if not ch or ch.get('refunded') or ch.get('settled'):
        return
    key = 'images' if state['provider'] == 'nexorai' else 'points'
    accounting(state, state['task_dir'], 'refund', -ch[key],
               {key: ch[key], **({'date': ch['date']} if key == 'images' else {})}, {'refunded': True})


def settle(state, task, actual, fields=None):
    ch = state.get('charge')
    if not ch or ch.get('settled') or ch.get('refunded'):
        return
    if amount(actual) < 0:
        fail('negative actual cost')
    delta = amount(actual) - amount(ch['points'])
    accounting(state, task, 'settle', delta,
               {'estimated_points': ch['points'], 'actual_points': float(amount(actual)),
                'adjusted': float(-delta), **(fields or {})},
               {'settled': True, 'actual_points': float(amount(actual)), 'adjusted': float(-delta)})
