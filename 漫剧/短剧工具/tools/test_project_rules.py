"""New independent projects inherit live rules, never a prior film's authority."""
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_materials
import codex_workflow as wf
import make_sample_project
import project_rules as rules


class ProjectRules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='new-series-rules-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def new_project(self, name, registered=False):
        root = self.base / '作品' / name
        root.mkdir(parents=True)
        config = copy.deepcopy(make_sample_project.PROJECT)
        config.update(title=name, genre='live-action', specification_status='proposal_pending_user_review')
        config.pop('source', None)
        (root / 'project.json').write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        for filename in ('assets.json', 'clips.json', 'voices.json'):
            (root / filename).write_text('[]\n', encoding='utf-8')
        if registered:
            (root / 'approvals').mkdir()
            (root / 'approvals/user-authorization.json').write_text('{"source":"SYNTHETIC_TEST_ONLY"}', encoding='utf-8')
            caps = dict(total_points=0, h3_requests=0, tts_requests=0, tts_characters=0,
                        images=0, video_revisions=0, revisions_per_clip=0)
            (root / 'approvals/execution-limits.json').write_text(json.dumps(dict(
                version=1, caps=caps, retry_unknown=False, allowed_resolutions=['768P'])), encoding='utf-8')
            wf.Work.initialize(root, 'new-' + name, ['Clip00001'], 8000, 0, simulation=True)
        return root

    def test_registration_attaches_current_shared_sources_without_copying_authority(self):
        for name in ('A', 'B'):
            root = self.new_project(name, registered=True)
            text = (root / 'AGENTS.md').read_text(encoding='utf-8')
            links = re.findall(r'\]\(<([^>]+)>\)', text)
            self.assertGreaterEqual(len(links), 7)
            self.assertTrue(all(Path(link).is_file() and Path(link).is_relative_to(rules.TOOLS_ROOT) for link in links))
            self.assertIn('genres/live-action.md', rules.rule_sources(root))
            self.assertIn('tools/VOICE_CLOSEOUT.md', rules.rule_sources(root))
            self.assertIn('tools/SHOT_CONTEXT.md', rules.rule_sources(root))
            self.assertIn('每段制作前读取当前状态', text)
            self.assertIn('首次说话视频通过后同轮提取、登记并送审音色', text)
            self.assertFalse((root / 'OVERRIDES.md').exists())
            self.assertFalse((root / 'approvals/codex-live-ready.json').exists())
            self.assertFalse((root / 'approvals/canvas-reviews.json').exists())
            self.assertEqual(json.loads((root / 'voices.json').read_text()), [])
            self.assertEqual(json.loads((root / 'approvals/execution-limits.json').read_text())['caps']['total_points'], 0)

    def test_manual_project_install_needs_no_media_approval(self):
        root = self.new_project('manual')
        before = (root / 'project.json').read_bytes()
        command = [sys.executable, '-I', str(Path(rules.__file__)), '--project', str(root), '--install']
        result = subprocess.run(command, capture_output=True, check=True)
        report = json.loads(result.stdout)
        self.assertEqual(report['mediaRequests'], 0)
        self.assertEqual(report['approvalChanges'], 0)
        self.assertTrue((root / 'AGENTS.md').is_file())
        self.assertFalse((root / 'approvals').exists())
        self.assertEqual((root / 'project.json').read_bytes(), before)

    def test_custom_project_instructions_and_repeat_install_are_preserved(self):
        root = self.new_project('custom')
        custom = '# 用户自定义\r\n每次完成电脑操作不要结束辅助进程。\r\n'
        (root / 'AGENTS.md').write_bytes(custom.encode('utf-8'))
        rules.ensure_entry(root)
        first = (root / 'AGENTS.md').read_bytes()
        rules.ensure_entry(root)
        self.assertEqual(first, (root / 'AGENTS.md').read_bytes())
        self.assertTrue(first.startswith(custom.encode('utf-8')))
        self.assertEqual(first.count(rules.BEGIN.encode()), 1)

    def test_shared_updates_are_seen_by_both_existing_and_new_projects(self):
        shared = self.base / 'shared-tools'
        for relative in (*rules.COMMON, 'genres/live-action.md'):
            destination = shared / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(rules.TOOLS_ROOT / relative, destination)
        first = self.new_project('first')
        with patch.object(rules, 'TOOLS_ROOT', shared):
            rules.ensure_entry(first)
            entry = (first / 'AGENTS.md').read_bytes()
            before = rules.rule_sources(first)
            source = shared / 'skill/references/post-production.md'
            source.write_text(source.read_text(encoding='utf-8') + '\nSynthetic new lesson.\n', encoding='utf-8')
            second = self.new_project('second')
            rules.ensure_entry(second)
            self.assertEqual(rules.rule_sources(first), rules.rule_sources(second))
            self.assertNotEqual(before['skill/references/post-production.md']['sha256'],
                                rules.rule_sources(first)['skill/references/post-production.md']['sha256'])
            self.assertEqual(entry, (first / 'AGENTS.md').read_bytes())

    def test_missing_shared_installation_does_not_write_partial_entry(self):
        root = self.new_project('missing')
        with patch.object(rules, 'TOOLS_ROOT', self.base / 'absent'):
            with self.assertRaises(FileNotFoundError):
                rules.ensure_entry(root)
        self.assertFalse((root / 'AGENTS.md').exists())

    def test_conflicting_managed_markers_do_not_overwrite_custom_content(self):
        root = self.new_project('conflict')
        original = ('user content\n' + rules.BEGIN).encode('utf-8')
        (root / 'AGENTS.md').write_bytes(original)
        with self.assertRaises(ValueError):
            rules.ensure_entry(root)
        self.assertEqual((root / 'AGENTS.md').read_bytes(), original)

    def test_exported_roles_use_live_entry_without_resetting_project_state(self):
        root = self.new_project('roles', registered=True)
        files = {p.relative_to(root): p.read_bytes() for p in root.rglob('*.json')}
        paths = codex_materials.export(root)
        self.assertEqual(len(paths), 5)
        for path in paths:
            text = Path(path).read_text(encoding='utf-8')
            self.assertIn(str(root / 'AGENTS.md'), text)
            self.assertNotIn('当前媒体入口锁定', text)
            self.assertNotIn('2026-11-07-Codex调度与五岗制作方案', text)
        for relative, original in files.items():
            self.assertEqual((root / relative).read_bytes(), original)
        self.assertFalse((root / '协作/会话.json').exists())

    def test_sample_defaults_match_current_shared_resolution_policy(self):
        root = make_sample_project.build(self.base / 'sample')
        video = json.loads((root / 'project.json').read_text(encoding='utf-8'))['video']
        self.assertEqual(video['default_resolution'], '768P')
        self.assertEqual(video['draft_resolution'], '768P')
        self.assertEqual(video['final_resolution'], '768P')
        self.assertFalse(video['use_context_ir'])


if __name__ == '__main__':
    unittest.main()
