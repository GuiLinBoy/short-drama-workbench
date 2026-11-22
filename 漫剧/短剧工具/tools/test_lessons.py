#!/usr/bin/env python3
"""lessons.py 的离线测试：临时目录 + 临时 git 仓库，不碰真实文件。运行：python -I test_lessons.py"""
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "lessons.py"

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8")

L_HEADER = "| 编号 | 日期 | 来源（作品 / 集） | 现象 | 规避方法 | 等级 | 次数 |\n|---|---|---|---|---|---|---|"

GENRE_MD = """# 3D 国漫题材

## 2. 模型

正文规则，不许动。

## 实战经验（复盘沉淀）

由总导演在复盘后提出、经你确认，再用 `tools/lessons.py` 写入（设计文档 §8.10）。只追加，不改写上面的正文规则。"观察"表示只出现过 1 次；出现 2 次以上或有明确证据的，才标为"规则"。

""" + L_HEADER + """

## 修订记录
| 日期 | 变更 |
|---|---|
| 2026-11-02 | 初版 |
"""

PITFALLS_MD = """# 坑与对策

| # | 现象 | 原因或推测 | 对策 | 来源 | 状态 |
|---|---|---|---|---|---|
| 1 | 现象一 | 原因 | 对策 | 官方 | 待验证 |
| 26 | 画面里复制出第二个同样的人 | 横移补位 | 写 Only Subject 1 appears | 社区 | 待验证 |

## 实战经验如何更新本表

由 lessons.py 更新。
"""


class LessonsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.manju = self.tmp / "漫剧"
        self.tools = self.manju / "短剧工具"                      # 真实布局：漫剧/短剧工具/，TOOLS.parent=漫剧/
        (self.tools / "genres").mkdir(parents=True)
        (self.tools / "skills/minimax-h3-video/references").mkdir(parents=True)
        (self.tools / "skills/nexorai-image").mkdir(parents=True)
        (self.manju).mkdir(exist_ok=True)
        (self.tools / "genres" / "3d-guoman.md").write_text(GENRE_MD, encoding="utf-8", newline="\n")
        (self.tools / "skills/minimax-h3-video/references/pitfalls.md").write_text(
            PITFALLS_MD, encoding="utf-8", newline="\n")
        (self.tools / "skills/nexorai-image" / "SKILL.md").write_text(
            "# NexorAI 生图\n\n## 实战经验\n\n" + L_HEADER + "\n", encoding="utf-8", newline="\n")
        (self.tools / "镜头语言手册.md").write_text(
            "# 镜头语言手册\n\n## 实战经验（复盘沉淀）\n\n" + L_HEADER + "\n", encoding="utf-8", newline="\n")
        self.env = {**os.environ, "LESSONS_TOOLS": str(self.tools), "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "init", "-q"], cwd=self.tools, check=True, capture_output=True, env=self.env)
        subprocess.run(["git", "-C", str(self.tools), "add", "-A"], check=True, capture_output=True, env=self.env)
        subprocess.run(["git", "-C", str(self.tools), "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-qm", "init"], check=True, capture_output=True, env=self.env)
        self.project = self.manju / "作品" / "样例作品"
        self.project.mkdir(parents=True)


    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_postproduction_experience_target_uses_transaction_and_can_retract(self):
        target=self.tools/'skill/references/post-production.md'
        target.parent.mkdir(parents=True)
        original='# 后期\n\n## 实战经验\n\n'+L_HEADER+'\n'
        target.write_text(original,encoding='utf-8',newline='\n')
        result=self.cli('add','--target','postproduction','--source','SYNTHETIC/EP001',
                        '--phenomenon','合成音频长度不足','--fix','逐段核对样本数后才拼接',
                        '--confirm','SYNTHETIC authorized maintenance')
        operation=json.loads(result.stdout)['written']
        self.assertIn(operation,target.read_text(encoding='utf-8'))
        self.cli('retract',operation,'--confirm','SYNTHETIC authorized undo')
        self.assertEqual(target.read_text(encoding='utf-8'),original)

    def cli(self, *args, ok=True, code=None):
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)],
                           capture_output=True, text=True, encoding="utf-8", env=self.env)
        out = r.stdout + r.stderr
        if code is not None:
            self.assertEqual(r.returncode, code, out)
        elif ok:
            self.assertEqual(r.returncode, 0, out)
        else:
            self.assertNotEqual(r.returncode, 0, out)
        return r

    def ledger(self):
        f = self.manju / "经验库.md"
        if not f.exists():
            return []
        return [l for l in f.read_text(encoding="utf-8").splitlines() if l.startswith("| L")]

    def genre_table(self):
        text = (self.tools / "genres" / "3d-guoman.md").read_text(encoding="utf-8")
        return [l for l in text.splitlines() if l.startswith("| L")]

    def git_log(self):
        r = subprocess.run(["git", "-C", str(self.tools), "log", "--oneline"],
                           capture_output=True, text=True, encoding="utf-8")
        return r.stdout.splitlines()

    # ---- add
    def test_add_to_genre_and_ledger_without_automatic_git_commit(self):
        r = json.loads(self.cli("add", "--target", "genre", "--genre", "3d-guoman",
                                "--source", "样例作品/EP001", "--phenomenon", "竖屏横移复制出第二个人",
                                "--fix", "单人镜头写 Only Subject 1 appears in the frame throughout.",
                                "--confirm", "可以，写进去").stdout)
        self.assertEqual(r["written"], "L001")
        rows = self.genre_table()
        self.assertEqual(len(rows), 1)
        self.assertIn("L001", rows[0])
        self.assertIn("观察", rows[0])
        led = self.ledger()
        self.assertEqual(len(led), 1)
        self.assertIn("可以，写进去", led[0])
        self.assertEqual(len(self.git_log()), 1)  # default writes must not commit
        self.assertIsNone(r["git"])

    def test_add_requires_confirm(self):
        r = self.cli("add", "--target", "genre", "--genre", "3d-guoman", "--source", "s",
                     "--phenomenon", "p", "--fix", "f", ok=False)
        self.assertIn("--confirm", r.stderr)
        self.assertEqual(self.genre_table(), [])
        self.assertEqual(self.ledger(), [])

    def test_add_rule_needs_evidence_or_two(self):
        self.cli("add", "--target", "genre", "--genre", "3d-guoman", "--source", "s",
                 "--phenomenon", "p", "--fix", "f", "--level", "规则", "--count", "1", ok=False)
        r = self.cli("add", "--target", "genre", "--genre", "3d-guoman", "--source", "s",
                     "--phenomenon", "p2", "--fix", "f2", "--level", "规则", "--count", "1",
                     "--evidence", "官方文档明确说明", ok=False)
        self.assertIn("--evidence", r.stderr)
        self.cli("add", "--target", "genre", "--genre", "3d-guoman", "--source", "s",
                 "--phenomenon", "p3", "--fix", "f3", "--level", "规则", "--count", "2",
                 "--confirm", "c")
        self.assertIn("规则", self.genre_table()[0])

    def test_add_dedup_rejects_same_summary(self):
        self.cli("add", "--target", "genre", "--genre", "3d-guoman", "--source", "s",
                 "--phenomenon", "竖屏横移复制出第二个人", "--fix", "f", "--confirm", "c")
        r = self.cli("add", "--target", "genre", "--genre", "3d-guoman", "--source", "s",
                     "--phenomenon", "竖屏横移复制出第二个人（again）", "--fix", "f2", "--confirm", "c", ok=False)
        self.assertIn("已有相似记录", r.stderr)
        self.assertEqual(len(self.genre_table()), 1)

    def test_add_twenty_cap(self):
        for i in range(20):
            self.cli("add", "--target", "genre", "--genre", "3d-guoman", "--source", f"s{i}",
                     "--phenomenon", f"现象-{i} 很独特", "--fix", f"f{i}", "--confirm", "c")
        self.assertEqual(len(self.genre_table()), 20)
        r = self.cli("add", "--target", "genre", "--genre", "3d-guoman", "--source", "s21",
                     "--phenomenon", "第21条现象", "--fix", "f", "--confirm", "c", ok=False)
        self.assertIn("上限 20", r.stderr)
        self.assertEqual(len(self.genre_table()), 20)

    def test_add_to_project_creates_file(self):
        self.cli("add", "--target", "project", "--project", self.project,
                 "--source", "样例作品/EP001", "--phenomenon", "角色特殊设定问题",
                 "--fix", "本作品内规避方法", "--confirm", "只用于本作品")
        f = self.project / "作品约定.md"
        self.assertTrue(f.is_file())
        self.assertIn("角色特殊设定问题", f.read_text(encoding="utf-8"))
        self.assertIn("只用于本作品", self.ledger()[0])          # 确认原话登记在经验库

    def test_add_invalid_level_rejected(self):
        r = self.cli("add", "--target", "genre", "--genre", "3d-guoman", "--source", "s",
                     "--phenomenon", "p", "--fix", "f", "--level", "已证实", "--confirm", "c", ok=False)
        self.assertIn("等级", r.stderr)

    def test_add_unknown_genre_rejected(self):
        self.cli("add", "--target", "genre", "--genre", "xuanhuan", "--source", "s",
                 "--phenomenon", "p", "--fix", "f", "--confirm", "c", ok=False)

    # ---- pitfall
    def test_pitfall_update_status(self):
        r = json.loads(self.cli("pitfall", "--row", "26", "--status", "已证实（样例作品 2 次）",
                                "--source", "样例作品", "--count", "2", "--confirm", "同意改状态").stdout)
        self.assertEqual(r["old"], "待验证")
        text = (self.tools / "skills/minimax-h3-video/references/pitfalls.md").read_text(encoding="utf-8")
        self.assertIn("| 26 | 画面里复制出第二个同样的人 | 横移补位 | 写 Only Subject 1 appears | 社区 | 已证实（样例作品 2 次） |", text)
        self.assertIn("状态：待验证 → 已证实", self.ledger()[0])

    def test_pitfall_new_row_numbering(self):
        r = json.loads(self.cli("pitfall", "--new-pitfall", "--source", "样例作品",
                                "--phenomenon", "新坑现象", "--fix", "新对策", "--confirm", "c").stdout)
        self.assertEqual(r["pitfall_no"], 27)
        self.assertIn("观察（1 次）", r["status"])
        self.cli("pitfall", "--new-pitfall", "--source", "样例作品", "--phenomenon", "第二坑现象",
                 "--fix", "对策2", "--level", "规则", "--count", "2", "--confirm", "c")
        text = (self.tools / "skills/minimax-h3-video/references/pitfalls.md").read_text(encoding="utf-8")
        self.assertIn("| 28 | 第二坑现象", text)
        self.assertIn("已证实（样例作品，2 次）", text)

    def test_pitfall_row_must_exist(self):
        self.cli("pitfall", "--row", "99", "--status", "x", "--source", "s", "--confirm", "c", ok=False)

    # ---- list
    def test_list_readonly(self):
        self.cli("add", "--target", "manual", "--source", "s", "--phenomenon", "镜头问题现象",
                 "--fix", "f", "--confirm", "c")
        r = json.loads(self.cli("list", "--target", "manual").stdout)
        self.assertEqual(r["rows"], 1)
        self.assertIn("L001", r["items"][0])
        r = json.loads(self.cli("list", "--target", "pitfall").stdout)
        self.assertEqual(r["rows"], 2)

    # ---- retract
    def test_retract_removes_row_and_marks_ledger(self):
        self.cli("add", "--target", "genre", "--genre", "3d-guoman", "--source", "s",
                 "--phenomenon", "要撤的现象", "--fix", "f", "--confirm", "c")
        self.assertEqual(len(self.genre_table()), 1)
        r = json.loads(self.cli("retract", "L001", "--confirm", "撤掉吧").stdout)
        self.assertEqual(self.genre_table(), [])
        self.assertIn("已撤销", self.ledger()[0])

    def test_retract_requires_confirm_and_exist(self):
        self.cli("retract", "L001", ok=False)
        self.cli("add", "--target", "genre", "--genre", "3d-guoman", "--source", "s",
                 "--phenomenon", "x现象", "--fix", "f", "--confirm", "c")
        self.cli("retract", "L001", "--confirm", "撤")
        self.cli("retract", "L001", "--confirm", "再撤", ok=False)     # 已撤销过
        self.cli("retract", "L999", "--confirm", "撤", ok=False)       # 不存在

    # ---- 正文保护
    def test_body_rules_not_touched(self):
        before = (self.tools / "genres" / "3d-guoman.md").read_text(encoding="utf-8").split("## 实战经验")[0]
        self.cli("add", "--target", "genre", "--genre", "3d-guoman", "--source", "s",
                 "--phenomenon", "p现象", "--fix", "f", "--confirm", "c")
        after = (self.tools / "genres" / "3d-guoman.md").read_text(encoding="utf-8").split("## 实战经验")[0]
        self.assertEqual(before, after)                                # 正文一个字没动


if __name__ == "__main__":
    unittest.main(verbosity=2)
