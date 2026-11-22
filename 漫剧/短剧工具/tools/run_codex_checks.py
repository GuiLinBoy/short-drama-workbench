"""Run isolated existing regressions plus Codex fixtures, with no real endpoints."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
SUITES = (
    'tools/test_project_rules.py',
    'tools/test_codex_workflow.py',
    'tools/test_codex_app_receipts.py',
    'tools/test_authorized_cli.py', 'tools/test_proposal_sink.py',
    'tools/test_cli_proposal_sink.py', 'tools/test_acceptance_jobs.py',
    'tools/test_execution_limits.py', 'tools/test_generation_regressions.py',
    'tools/test_h3_billing_contract.py', 'tools/test_wait_generation.py',
    'tools/test_voice_readiness.py', 'tools/test_voice_lifecycle.py', 'tools/test_authorized_revoice.py', 'tools/test_shot_context.py', 'tools/test_h3_execution_checks.py',
    'tools/test_tools.py', 'skills/minimax-h3-video/scripts/test_h3_task.py',
    'skills/nexorai-image/scripts/test_nexorai_image.py',
    'tools/audio/test_narrate.py', 'tools/audio/test_align.py',
    'tools/audio/test_assemble.py', 'tools/audio/test_postproduction.py',
    'tools/audio/test_edit_plan.py', 'tools/audio/test_delivery_checks.py',
    'tools/audio/test_sync_integrity_media.py',
    'tools/audio/test_postproduction_media.py', 'tools/test_lessons.py',
    'tools/test_lessons_transactions.py', 'tools/test_bots_skills.py',
    'tools/dryrun/test_dryrun.py',
)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=SUITES, action='append')
    args = parser.parse_args()
    suites = args.suite or SUITES
    out = ROOT / '.offline-checks' / ('codex-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    out.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if not any(x in k.upper() for x in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'ASR_FAKE_RESULT', 'LESSONS_TOOLS'))}
    env.update(MINIMAX_H3_API_KEY='CODEX-OFFLINE-FAKE-ONLY',
               NEXORAI_API_KEY='CODEX-OFFLINE-FAKE-ONLY',
               H3_BASE_URL='http://127.0.0.1:9', NEXORAI_BASE_URL='http://127.0.0.1:9/v1',
               HF_HUB_OFFLINE='1', MODELSCOPE_OFFLINE='1', PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    results = []
    for suite in suites:
        script = 'import sys,runpy;sys.path.insert(0,' + repr(str(ROOT / 'tools')) + ');runpy.run_path(' + repr(str(ROOT / suite)) + ',run_name="__main__")'
        try:
            p = subprocess.run([sys.executable, '-I', '-c', script], cwd=ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
            output, code = p.stdout, p.returncode
        except subprocess.TimeoutExpired as error:
            output, code = (error.stdout or b'') + b'\nTIMEOUT\n', -1
        log = out / (suite.replace('/', '__') + '.log')
        log.write_bytes(output)
        text = output.decode('utf-8', 'replace')
        count = re.search(r'Ran (\d+) tests?', text)
        skips = re.search(r'skipped=(\d+)', text)
        result = dict(suite=suite, returncode=code, tests=int(count[1]) if count else None,
                      skipped=int(skips[1]) if skips else 0, log=str(log))
        results.append(result)
        print(json.dumps({k: v for k, v in result.items() if k != 'log'}, ensure_ascii=True), flush=True)
    report = dict(mode='OFFLINE_FIXTURES_NO_REAL_MODEL_OR_MEDIA', results=results,
                  all_passed=all(r['returncode'] == 0 for r in results),
                  tests=sum(r['tests'] or 0 for r in results), skipped=sum(r['skipped'] for r in results),
                  real_model_requests=0, real_media_requests=0,
                  limits='Local regression evidence only; real app threads, paid providers and film quality are not verified.')
    (out / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                                     encoding='utf-8', newline='\n')
    print(json.dumps({'report': str(out / 'results.json'), 'all_passed': report['all_passed'],
                      'tests': report['tests'], 'skipped': report['skipped']}, ensure_ascii=True), flush=True)
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
