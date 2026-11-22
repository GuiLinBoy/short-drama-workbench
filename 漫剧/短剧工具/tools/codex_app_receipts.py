"""Normalize an actual read_thread final response, not a compact wait snapshot.

The authorized controller obtains the response from codex-app-tools. This parser
does not authenticate arbitrary caller-supplied JSON or start another model turn.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_workflow as wf


def accept(work, task_id, response, expected_turn_id, *, assets=None):
    task = work.task(task_id)
    wf.require(type(response.get('schemaVersion')) is int and response['schemaVersion'] == 1,
               'unsupported app observation schema')
    thread = response.get('thread', {})
    wf.require(thread.get('kind') == 'codex' and thread.get('id') == task['threadId'] and
               thread.get('hostId') == task['hostId'], 'app observation belongs to another thread/host')
    turns = response.get('turns')
    wf.require(isinstance(turns, list), 'full read_thread observation required; compact snapshots are not final source')
    found = [turn for turn in turns if turn.get('id') == expected_turn_id]
    wf.require(len(found) == 1, 'exact observed turn missing or duplicated')
    turn = found[0]
    wf.require(turn.get('status') == 'completed' and turn.get('error') is None, 'turn has not completed successfully')
    finals = [item for item in turn.get('items', [])
              if item.get('type') == 'agentMessage' and item.get('phase') == 'final_answer']
    wf.require(len(finals) == 1 and not finals[0].get('truncated'), 'unique untruncated final response required')
    final = finals[0]
    raw = final.get('text')
    wf.require(isinstance(raw, str) and raw, 'missing original final response')
    # Parse unchanged text; do not repair a cut-off JSON reply or invent defaults.
    wf.data.decode(raw)
    normalized = dict(source='CODEX_APP_FINAL_RESPONSE', threadId=thread['id'], hostId=thread['hostId'],
                      turnId=turn['id'], taskId=task_id,
                      inputsSha256=wf.data.sha(wf.data.canonical(task['inputs'])),
                      status='completed', finalSha256=wf.data.sha(raw.encode('utf-8')),
                      simulation=task['simulation'])
    record = work.complete(task_id, raw, normalized, assets=assets)
    evidence = dict(sourceTool='codex-app-tools.read_thread', threadId=thread['id'], hostId=thread['hostId'],
                    title=thread.get('title'), turnId=turn['id'], messageId=final.get('id'),
                    responseSha256=wf.data.sha(wf.data.canonical(response)),
                    normalized=normalized, finalText=raw,
                    commands=[{k: item.get(k) for k in ('id', 'status', 'exitCode')}
                              for item in turn.get('items', []) if item.get('type') == 'commandExecution'])
    path = wf.inside(work.root, '协作/观察/' + task_id + '-app-read-final.json')
    with work.lock():
        if path.exists():
            wf.require(wf.verified(path) == evidence, 'app final observation changed; no overwrite')
        else:
            wf.create(path, evidence)
    return record


if __name__ == '__main__':
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--turn-id', required=True)
    parser.add_argument('--read-thread-json', required=True)
    args = parser.parse_args()
    try:
        work = wf.Work(args.project)
        source = wf.read(wf.inside(work.root, args.read_thread_json))
        record = accept(work, args.task_id, source, args.turn_id)
        print(json.dumps({k: record[k] for k in ('workId', 'taskId', 'state')}, ensure_ascii=True))
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit('STOP: ' + str(error))
