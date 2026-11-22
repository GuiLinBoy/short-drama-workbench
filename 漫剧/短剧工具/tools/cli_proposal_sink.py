"""Trusted controller's native CLI result adapter; never an Agent tool.

All bindings, scope state and Core tool-call rows must be obtained by the
controller from nex_bot, not supplied by a model. No network, CLI launch,
production execution, credentials, approvals or fabricated Core reports.
"""
from pathlib import Path

import proposal_sink as sink

SOURCE = 'CLI_NATIVE_RESULT'
USAGE = 'CLI_REPORTED_TOKENS_NOT_PHYSICAL_REQUESTS'
IDENTITY = ('bindingId', 'botId', 'originTaskId', 'sessionId', 'nativeSessionId', 'nativeTurnId')
FINAL_FIELDS = ('source', 'reportId', *IDENTITY, 'status', 'completionEvidence', 'content',
                'contentSha256', 'contentBytes', 'tokenUsage', 'usageSemantics', 'simulation')


def object_json(value):
    result = sink.decode(value) if isinstance(value, str) else value
    sink.require(isinstance(result, dict), 'trusted Core tool DTO must contain JSON objects')
    return result


def one(items, identifier, message):
    sink.require(isinstance(items, list) and all(isinstance(item, dict) for item in items), message)
    found = [item for item in items if item.get('id') == identifier]
    sink.require(len(found) == 1, message)
    return found[0]


def trusted_call(status, call, bot_id):
    sink.require(isinstance(call, dict), 'actual trusted Core tool-call DTO is required')
    for key in ('id', 'task_id', 'bot_id'):
        sink.require(isinstance(call.get(key), str) and sink.IDENTIFIER.fullmatch(call[key]),
                     'invalid trusted Core call identity')
    projection = one(status.get('operations'), call['id'], 'exact successful scope operation is required')
    fields = ('id', 'task_id', 'bot_id', 'tool_name', 'status')
    sink.require(all(call.get(k) == projection.get(k) for k in fields) and
                 call.get('status') == 'succeeded' and call.get('tool_name') == 'device.agent' and
                 call['bot_id'] == bot_id, 'Core call differs from successful scoped device.agent operation')
    task = one(status.get('tasks'), call['task_id'], 'exact observed Core task is required')
    sink.require(task.get('bot_id') == bot_id and task.get('status') == 'succeeded',
                 'Core call task has not completed for this member')
    return object_json(call.get('arguments_json')), object_json(call.get('result_json'))


def store_cli_proposal(root, bindings, status, observed_call, assets, *, origin_call=None):
    """Validate actual native completion and append a data-only versioned receipt.

    The observation is a successful status call, separate from the initiating
    approved-job start. Neither a start acknowledgement nor Core task success is
    accepted in place of the native turn-completed event and its complete JSON.
    """
    root = Path(root).resolve()
    sink.require(root.is_dir() and Path(bindings.get('root', '')).resolve() == root,
                 'trusted binding root mismatch')
    scope_id = bindings.get('scopeId')
    sink.require(isinstance(scope_id, str) and sink.IDENTIFIER.fullmatch(scope_id), 'invalid trusted scope')
    members = bindings.get('members')
    sink.require(isinstance(members, list) and len(members) == 5 and
                 all(isinstance(m, dict) and isinstance(m.get('id'), str) and
                     sink.IDENTIFIER.fullmatch(m['id']) for m in members), 'five trusted fresh members required')
    ids = [member['id'] for member in members]
    sink.require(len(set(ids)) == 5, 'trusted member IDs must be unique')
    current = status.get('members')
    sink.require(status.get('scopeId') == scope_id and isinstance(current, list) and len(current) == 5 and
                 all(isinstance(m, dict) for m in current) and {m.get('id') for m in current} == set(ids),
                 'scope status differs from trusted fresh membership')
    device = bindings.get('cliDevice')
    sink.shape(device, ('id', 'bindingId'))
    sink.text(device['id'], 1, 200); sink.text(device['bindingId'], 1, 300)
    sink.require(isinstance(observed_call, dict) and observed_call.get('bot_id') in ids,
                 'observation is not a member tool call')
    args, snapshot = trusted_call(status, observed_call, observed_call['bot_id'])
    final = snapshot.get('finalResult')
    sink.shape(final, FINAL_FIELDS)
    sink.require(snapshot.get('executionMode') == 'restricted-inference' and
                 type(final['simulation']) is bool, 'explicit restricted mode and simulation marking required')
    sink.require(final['source'] == SOURCE and final['status'] == 'completed' and
                 snapshot.get('status') == 'completed' and
                 final['completionEvidence'] == 'native-turn-completed',
                 'complete native turn evidence is required')
    for key in IDENTITY:
        sink.text(final[key], 1, 300)
        sink.require(final[key] == snapshot.get(key), 'native completion and snapshot identity differ')
    sink.require(final['botId'] == observed_call['bot_id'] and final['bindingId'] == device['bindingId'],
                 'native result belongs to another member or device binding')
    sink.require(args.get('deviceId') == device['id'] and args.get('operation') == 'status',
                 'completion must come from the bound device status operation')
    parameters = args.get('parameters')
    sink.require(isinstance(parameters, dict) and parameters.get('sessionId') == final['sessionId'],
                 'status call queried another native session')
    sink.require(isinstance(snapshot.get('cwd'), str) and Path(snapshot['cwd']).resolve() == root,
                 'native CLI project root differs from trusted binding')

    launch_args, launch_result = trusted_call(status, origin_call, final['botId'])
    sink.require(origin_call['task_id'] == final['originTaskId'] and
                 launch_args.get('deviceId') == device['id'] and
                 launch_result.get('sessionId') == final['sessionId'] and
                 isinstance(launch_result.get('cwd'), str) and Path(launch_result['cwd']).resolve() == root,
                 'native completion is not tied to the actual launch task and device')
    sink.require(launch_result.get('executionMode') == 'restricted-inference',
                 'legacy unrestricted CLI launch is not authorized for this sink')
    launch = launch_args.get('parameters')
    sink.require(isinstance(launch, dict), 'launch parameters missing')
    sink.shape(launch, ('agent', 'jobId'))
    sink.require(launch_args.get('operation') == 'start' and origin_call['id'] == final['sessionId'] and
                 launch.get('agent') == 'codex', 'restricted native origin must be an approved Codex job start')
    approved = one(bindings.get('cliJobs'), launch.get('jobId'), 'exact trusted CLI job is required')
    sink.require(approved.get('botId') == final['botId'] and approved.get('taskId') == final['originTaskId'] and
                 isinstance(approved.get('root'), str) and Path(approved['root']).resolve() == root,
                 'CLI job differs from approved role, task or project')

    raw = final['content']
    sink.text(raw, 2, 20000)
    encoded = raw.encode('utf-8')
    sink.require(type(final['contentBytes']) is int and final['contentBytes'] == len(encoded) and
                 final['contentSha256'] == sink.sha(encoded), 'native final UTF-8 bytes or digest changed')
    report_identity = [final[k] for k in IDENTITY] + [final['contentSha256']]
    report_id = sink.sha(sink.canonical(report_identity))
    sink.require(final['reportId'] == report_id, 'native report identity changed')
    sink.require(final['usageSemantics'] == USAGE, 'CLI usage must not be converted into physical requests')
    usage = final['tokenUsage']
    if usage is not None:
        sink.shape(usage, ('inputTokens', 'cachedInputTokens', 'outputTokens', 'totalTokens'))
        for count in usage.values(): sink.integer(count, 0, 2**53 - 1)
    value = sink.validate_proposal(sink.decode(raw), root, assets)
    member = next(m for m in members if m['id'] == final['botId'])
    sink.require(value['kind'] in member.get('allowedKinds', []), 'role is not allowed this proposal kind')
    if value['kind'] == 'technical-review':
        sink.require(all(assets[a['id']].get('reviewTaskId') == final['originTaskId']
                         for a in value['data']['artifacts']), 'review lacks evidence for this actual native task')
    origin = {key: final[key] for key in ('source', *IDENTITY, 'completionEvidence', 'contentBytes',
                                         'contentSha256', 'tokenUsage', 'usageSemantics', 'simulation')}
    origin['executionMode'] = snapshot['executionMode']
    origin['approvedJobId'] = launch['jobId']
    # A completed native turn is immutable even if its task attribution or report
    # ID is changed. A different turn may legitimately create a new version.
    origin['runKey'] = sink.sha(sink.canonical([final[k] for k in IDENTITY if k != 'originTaskId']))
    return sink.persist_validated_proposal(root, scope_id, final['botId'], final['originTaskId'],
                                          report_id, value, raw, origin=origin)
