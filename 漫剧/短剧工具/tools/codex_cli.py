"""Codex local tool entry. Offline by default; paid/network work needs readiness.

Uses existing tool, runtime-pin and media-accounting implementations. The
controller records actual user spending approval separately from test success.
No model/CLI process is started by this entry.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import authorized_cli as legacy
import codex_workflow as wf

LOCAL = {'h3': {'prepare', 'set-continuity'}, 'image': {'prepare', 'resume'},
         'tts': {'prepare'}, 'align': {'align', 'check', 'subtitle', 'cut-voice'}}


@contextlib.contextmanager
def offline_network():
    """Block network in trusted local Python tools, not an OS network sandbox."""
    original_open = urllib.request.urlopen
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create = socket.create_connection
    def refused(*args, **kwargs):
        raise ValueError('network disabled for offline Codex tool operation')
    urllib.request.urlopen = refused
    socket.socket.connect = refused
    socket.socket.connect_ex = refused
    socket.create_connection = refused
    try:
        yield
    finally:
        urllib.request.urlopen = original_open
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
        socket.create_connection = original_create


def network_operation(tool, arguments):
    if tool in ('state', 'validate', 'tail', 'assemble'):
        return False
    return not arguments or arguments[0] not in LOCAL.get(tool, set())


def budget_policy_digest(path):
    """Pin spending authority while allowing the accounting journal to advance."""
    from decimal import Decimal, InvalidOperation
    rows = wf.read(path)
    wf.require(isinstance(rows, list) and rows, 'budget batches required')
    names, policy = set(), []
    for row in rows:
        wf.require(isinstance(row, dict) and isinstance(row.get('batch'), str) and
                   row['batch'] not in names, 'invalid or duplicated budget batch')
        names.add(row['batch'])
        operations = row.get('_operations', {})
        pending = row.get('pending_settlements', {})
        wf.require(isinstance(operations, dict) and isinstance(pending, dict), 'invalid accounting journal')
        def number(value):
            wf.require(type(value) in (int, float), 'invalid accounting amount')
            try:
                amount = Decimal(str(value))
            except InvalidOperation as error:
                raise ValueError('invalid accounting amount') from error
            wf.require(amount.is_finite(), 'nonfinite accounting amount')
            return amount
        used, limit = number(row.get('used', 0)), number(row.get('limit'))
        wf.require(0 <= used <= limit and
                   all(isinstance(key, str) and key for key in operations) and
                   sum((number(v) for v in operations.values()), Decimal(0)) == used and
                   all(isinstance(key, str) and key and number(v) >= 0 for key, v in pending.items()),
                   'budget usage differs from its accounting journal')
        policy.append({key: value for key, value in row.items()
                       if key not in ('used', '_operations', 'pending_settlements')})
    return wf.data.sha(wf.data.canonical(policy))


def runtime_pin_paths(root):
    """Current controller pins; refreshing these is local verification, not spending approval."""
    root = Path(root).resolve()
    return {'budget': root / 'approvals/budget.json',
            'limits': root / 'approvals/execution-limits.json',
            'runtime': root / 'approvals/runtime-tools.json',
            'entry': Path(__file__).resolve(),
            'workflow': Path(wf.__file__).resolve(),
            'launcher': Path(legacy.__file__).resolve(),
            **{name: Path(__file__).resolve().parent / (filename + '.py') for name, filename in {
                'generation_runtime': 'generation_runtime', 'execution_limits': 'execution_limits',
                'proposal_sink': 'proposal_sink', 'codex_jobs': 'codex_jobs', 'recipes': 'acceptance_jobs',
                'app_receipts': 'codex_app_receipts', 'canvas_review': 'canvas_review',
                'voice_readiness': 'voice_readiness', 'voice_lifecycle': 'voice_lifecycle', 'shot_context': 'shot_context', 'h3_execution_checks': 'h3_execution_checks',
            }.items()},
            **{'target_' + name: path for name, path in legacy.TARGETS.items()}}


def validate_live(work, tool, arguments):
    """Fail before credentials/network. A bare flag/file cannot enable spending."""
    root = work.root
    wf.require(not work.spec['simulation'], 'simulated work cannot use live services')
    ready_path = wf.inside(root, 'approvals/codex-live-ready.json')
    wf.require(ready_path.is_file(), 'spending approval and verified Codex readiness are not installed')
    ready = wf.read(ready_path)
    wf.require(ready.get('version') == 1 and ready.get('workId') == work.spec['workId'] and
               ready.get('root') == str(root), 'readiness belongs to another work')
    wf.require(ready.get('checks') == {
        'offlineIsolation': True, 'mediaLimits': True, 'receiptAssociation': True,
        'unknownNoRepost': True, 'runtimePins': True}, 'readiness checks incomplete')
    approval_rel = ready.get('approvalFile')
    wf.require(isinstance(approval_rel, str) and approval_rel.startswith('approvals/'),
               'local spending approval reference required')
    approval_path = wf.inside(root, approval_rel)
    wf.require(wf.digest(approval_path) == ready.get('approvalSha256'), 'spending approval changed')
    approval = wf.read(approval_path)
    wf.require(approval.get('status') == 'USER_APPROVED_CODEX_MEDIA_EXECUTION' and
               approval.get('workId') == work.spec['workId'] and
               approval.get('approved') is True and
               isinstance(approval.get('userQuote'), str) and approval['userQuote'].strip(),
               'actual user media approval must be recorded by controller')
    operation = tool + ':' + (arguments[0] if arguments else '')
    wf.require(operation in approval.get('allowedOperations', []), 'network operation not approved')
    expected = runtime_pin_paths(root)
    wf.require(isinstance(ready.get('pins'), dict) and set(ready['pins']) == set(expected),
               'readiness pins incomplete')
    wf.require(all((budget_policy_digest(path) if name == 'budget' else wf.digest(path)) == ready['pins'][name]
                   for name, path in expected.items()),
               'approved code/configuration changed; reverify before use')


def image_exception_arguments(tool, arguments, work):
    """One human-approved image attempt; never a general unknown retry switch."""
    if '--after-unknown' not in arguments:
        return arguments
    wf.require(tool == 'image' and arguments[0] == 'submit' and '--execute' in arguments and
               arguments.count('--after-unknown') == 1, 'unknown exception does not cover this operation')
    allowed = {'--request', '--task-dir', '--execute', '--budget', '--batch', '--after-unknown'}
    wf.require(all(not item.startswith('--') or item in allowed for item in arguments),
               'unknown exception has extra options')
    def value(flag):
        wf.require(arguments.count(flag) == 1 and arguments.index(flag) + 1 < len(arguments),
                   'unknown exception requires one exact option value')
        return arguments[arguments.index(flag) + 1]
    approval_path = wf.inside(work.root, 'approvals/user-authorization-amendment-20261107-image-attempt002.json')
    approval = wf.read(approval_path)
    ready = wf.read(wf.inside(work.root, 'approvals/codex-live-ready.json'))
    wf.require(wf.digest(approval_path) == ready.get('oneImageExceptionApprovalSha256'),
               'exact unknown exception approval is not pinned by controller readiness')
    wf.require(not work.spec['simulation'] and approval.get('status') == 'USER_APPROVED_ONE_IMAGE_UNKNOWN_EXCEPTION' and
               approval.get('approved') is True and approval.get('workId') == work.spec['workId'] and
               approval.get('userQuote') == '批准，仅本次精确例外，原6张总上限不变',
               'exact human approval for this unknown exception is missing')
    old_relative = 'tasks/image-C00001-A-image/attempt-001'
    new_relative = 'tasks/image-C00001-A-image/attempt-002'
    request_relative = 'requests/C00001-A-image.json'
    old_path = wf.inside(work.root, old_relative + '/state.json')
    request_path = wf.inside(work.root, request_relative)
    old = wf.read(old_path)
    wf.require(approval.get('oldAttempt') == old_relative and approval.get('newAttempt') == new_relative and
               approval.get('requestPath') == request_relative and
               wf.digest(old_path) == approval.get('oldStateSha256') and old.get('status') == 'unknown' and
               old.get('provider') == 'nexorai' and
               wf.digest(request_path) == approval.get('requestSha256') == old.get('request_sha256'),
               'approved unknown state or source request changed')
    wf.require((work.root / value('--task-dir')).resolve() == wf.inside(work.root, new_relative) and
               (work.root / value('--request')).resolve() == request_path and
               (work.root / value('--budget')).resolve() == work.root / 'approvals/budget.json' and
               value('--batch') == 'EP001-参考图' and value('--after-unknown') == 'attempt-001',
               'exception belongs only to the approved second character-image attempt')
    request = wf.read(request_path)
    wf.require(request.get('provider') == 'nexorai' and request.get('project') == str(work.root) and
               request.get('asset') == 'C00001-A' and request.get('kind') == 'image' and
               request.get('target') == '角色/C00001-A.png' and request.get('ratio') == '9:16' and
               request.get('estimate_images') == 1 and
               request.get('params') == {'model': 'grok-imagine-image-quality', 'n': 1, 'aspect_ratio': '9:16'},
               'exception request differs from the single approved image')
    # The legacy guard still denies the flag everywhere else. Only its validation
    # view omits this validated pair; the real provider argv retains it.
    index = arguments.index('--after-unknown')
    return arguments[:index] + arguments[index + 2:]


def validate(tool, arguments, work):
    checked_arguments = image_exception_arguments(tool, arguments, work)
    resuming = tool == 'image' and checked_arguments and checked_arguments[0] == 'resume'
    if resuming:
        # Local bookkeeping only, but still requires exact user-approved readiness.
        validate_live(work, tool, checked_arguments)
        wf.require(len(checked_arguments) == 7 and checked_arguments[1] == '--project'
                   and checked_arguments[3] == '--model' and checked_arguments[5] == '--note',
                   'only an exact project/model/note quota resumption is allowed')
        ready = wf.read(wf.inside(work.root, 'approvals/codex-live-ready.json'))
        approval = wf.read(wf.inside(work.root, ready['approvalFile']))
        model = checked_arguments[4]
        wf.require(model == approval.get('quotaResumeModel') and model == 'gpt-image-2.5'
                   and str(work.root) == checked_arguments[2] and checked_arguments[6].strip(),
                   'quota resumption differs from the separately approved GPT scope')
    legacy.validate_arguments(tool, checked_arguments, work.root, require_legacy_readiness=False,
                              allow_quota_resumption=bool(resuming))
    if tool == 'tts' and arguments and arguments[0] == 'prepare':
        wf.require('--catalog' in arguments, 'offline speech preparation requires a saved voice catalog')
    online = network_operation(tool, arguments)
    if online:
        validate_live(work, tool, arguments)
        if tool in legacy.KEY_NAMES and arguments and arguments[0] == 'submit' and '--execute' in arguments:
            wf.require('--request' in arguments, 'prepared request required')
            request_path = (work.root / arguments[arguments.index('--request') + 1]).resolve()
            request = wf.read(wf.inside(work.root, request_path.relative_to(work.root).as_posix()))
            if tool == 'tts':
                wf.require('--entry' in arguments, 'speech entry required')
                entry = arguments[arguments.index('--entry') + 1]
                matches = [item for item in request['requests'] if item['entry_id'] == entry]
                wf.require(len(matches) == 1, 'unique speech entry required')
                request = matches[0]
            wf.require(not request.get('warnings'), 'prepared request has unresolved warnings; do not submit')
        if tool in ('h3', 'image') and arguments and arguments[0] == 'submit' and '--execute' in arguments:
            from canvas_review import ReviewStore
            wf.require(all(flag in arguments for flag in ('--request', '--batch', '--task-dir')),
                       'canvas review requires an exact request, batch and attempt')
            ReviewStore(work.root).require_generation(tool,
                work.root / arguments[arguments.index('--request') + 1],
                arguments[arguments.index('--batch') + 1],
                work.root / arguments[arguments.index('--task-dir') + 1])
            if tool == 'h3':
                from voice_readiness import require_voice_readiness
                from h3_execution_checks import require_execution_checks
                require_voice_readiness(work.root,
                    work.root / arguments[arguments.index('--request') + 1],
                    work.root / arguments[arguments.index('--task-dir') + 1],
                    arguments[arguments.index('--batch') + 1])
                require_execution_checks(work.root,
                    work.root / arguments[arguments.index('--request') + 1],
                    work.root / arguments[arguments.index('--task-dir') + 1],
                    arguments[arguments.index('--batch') + 1])
    return online


def execute(project, tool, arguments):
    work = wf.Work(project)
    online = validate(tool, arguments, work)  # Must precede user_key and any service access.
    for name, expected in (('H3_BASE_URL', 'https://cp.compshare.cn'),
                           ('NEXORAI_BASE_URL', 'https://api.nexorai.com.cn/v1')):
        wf.require(os.environ.get(name, expected).rstrip('/') == expected,
                   'live endpoint override refused before credentials')
    approved = legacy.runtime_tools(work.root)
    legacy.checked_binary(sys.executable, approved)
    previous = Path.cwd()
    original_env = os.environ.copy()
    original_argv = sys.argv
    original_sys_path = sys.path[:]
    try:
        # Only known non-secret environment fields go into local tools/children.
        sanitized = legacy.clean_environment(original_env)
        os.environ.clear()
        os.environ.update(sanitized)
        os.environ['PATH'] = os.pathsep.join(dict.fromkeys(str(Path(v['path']).parent) for v in approved.values()))
        os.chdir(work.root)
        legacy.local_recognition_environment(tool, arguments)
        variable = legacy.KEY_NAMES.get(tool) if online else None
        secret = None
        if variable:
            # user_key can use the process value without exposing it; offline never calls it.
            if variable in original_env:
                os.environ[variable] = original_env[variable]
            secret = legacy.user_key(variable)
            if secret:
                os.environ[variable] = secret
        network = legacy.authenticated_requests() if online else offline_network()
        with contextlib.redirect_stdout(legacy.RedactedStream(sys.stdout, [secret])), \
             contextlib.redirect_stderr(legacy.RedactedStream(sys.stderr, [secret])), \
             legacy.guarded_children(work.root, Path(__file__).resolve()), network:
            sys.path.insert(0, str(legacy.TARGETS[tool].parent))
            sys.argv = [str(legacy.TARGETS[tool]), *arguments]
            runpy.run_path(str(legacy.TARGETS[tool]), run_name='__main__')
    finally:
        os.environ.clear()
        os.environ.update(original_env)
        os.chdir(previous)
        sys.argv = original_argv
        sys.path[:] = original_sys_path


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-scope', required=True)
    parser.add_argument('tool', choices=legacy.TARGETS)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    a = parser.parse_args()
    execute(a.project_scope, a.tool, a.arguments)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit('STOP: ' + str(error))
