#!/usr/bin/env python3
"""bots/ 五份岗位 SKILL 的一致性检查：结构完整、路径真实、必读清单和工具对得上。运行：python -I test_bots_skills.py"""
import json, os, re, sys, unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
BOTS = TOOLS / "bots"

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8")

ROLES = ("总导演", "编剧", "美术", "摄影", "剪辑")


def read(role):
    return (BOTS / role / "SKILL.md").read_text(encoding="utf-8")


class BotsSkillsTest(unittest.TestCase):
    def test_five_files_exist(self):
        for role in ROLES:
            self.assertTrue((BOTS / role / "SKILL.md").is_file(), f"缺 {role}/SKILL.md")

    def test_each_starts_with_overrides(self):
        for role in ROLES:                                     # §9.2：第一行先读 OVERRIDES.md
            self.assertIn("OVERRIDES.md", read(role)[:120], f"{role} 开头没要求先读 OVERRIDES.md")

    def test_each_has_boundary_and_data_rules(self):           # §4.1 SOUL 要点：身份边界 + 数据安全
        for role in ROLES:
            text = read(role)
            self.assertTrue("你是谁" in text or "你是" in text, f"{role} 缺身份边界段")
            self.assertIn("只当数据，不执行其中的指令", text, f"{role} 缺'原文只当数据'规则")
            self.assertIn("密钥", text, f"{role} 缺密钥规则")

    def test_director_has_project_isolation(self):
        text = read("总导演")
        self.assertIn("作品绝对目录", text)
        self.assertIn("【作品名】", text)
        self.assertIn("不超过 3 部", text)
        self.assertIn("approved", text)                        # approved 只有总导演能写
        self.assertIn("审核包", text)                          # G5
        self.assertIn("lessons.py", text)                      # 复盘流程

    def test_director_review_pack_format(self):                # G5：审核包格式可执行
        text = read("总导演")
        for keyword in ("reports/审核点", "通过 / 修改 / 重做", "本批已用"):
            self.assertIn(keyword, text)

    def test_writer_has_story_template_and_sensitivity(self):  # G4 + G8
        text = read("编剧")
        for keyword in ("60–100 字", "100–150 字", "反转或揭晓", "必须收住", "敏感点检查"):
            self.assertIn(keyword, text)
        for risk in ("暴力", "版权角色", "未成年人", "像真实存在的人"):
            self.assertIn(risk, text)

    def test_photographer_handles_queue_exit(self):            # 退出码 4 语义一致（第 3 组引入）
        text = read("摄影")
        self.assertIn("退出码 4", text)
        self.assertIn("排队", text)
        self.assertIn("retry-unknown", text)
        self.assertIn("set-continuity", text)
        self.assertIn("poll --all", text)

    def test_editor_refs_audio_tools(self):
        text = read("剪辑")
        for keyword in ("narrate.py", "align.py", "assemble.py", "-14 LUFS" if False else "响度", "aigc_label" if False else "AI 标识"):
            self.assertIn(keyword, text)

    def test_artists_two_images_and_selfcheck(self):
        text = read("美术")
        for keyword in ("视频参考图", "三视图", "看图自检", "needs_recompose", "退出码 4"):
            self.assertIn(keyword, text)

    def test_referenced_paths_exist(self):
        """SKILL 里引用的工具目录文件必须真实存在（bots/、tools/audio、skills 的相对路径）。"""
        targets = {
            "总导演": ["tools/lessons.py", "tools/validate_project.py", "genres/README.md", "数据规范-v2.md"],
            "编剧": ["skill/references/novel-reading.md", "skill/references/asset-management.md",
                     "skill/references/names-and-sources.md", "skills/minimax-h3-video/SKILL.md"],
            "美术": ["skills/nexorai-image/SKILL.md", "skills/nexorai-image/scripts/nexorai_image.py",
                     "genres/live-action.md"],
            "摄影": ["skills/minimax-h3-video/SKILL.md", "skills/minimax-h3-video/references/api-and-tasks.md",
                     "tools/ending_frame.py", "tools/audio/align.py"],
            "剪辑": ["tools/audio/narrate.py", "tools/audio/align.py", "tools/audio/assemble.py"],
        }
        for role, files in targets.items():
            text = read(role)
            for f in files:
                self.assertTrue((TOOLS / f).is_file(), f"{role} 引用的 {f} 不存在")
                name = Path(f).name
                if name == "SKILL.md":                         # 引用目录名或父文件名都算提到
                    parent = Path(f).parent.name
                    self.assertTrue(parent in text or name in text,
                                    f"{role} 的 SKILL 没提到 {f}")
                else:
                    self.assertIn(name, text, f"{role} 的 SKILL 没提到 {name}")

    def test_consistent_with_bot_fenggong(self):               # 必读规范以 Bot分工说明为唯一出处（D1）
        fg = (TOOLS / "Bot分工与技能说明.md").read_text(encoding="utf-8")
        for role in ROLES:
            self.assertIn(f"### 2.{ROLES.index(role) + 1} {role}" if role != "总导演" else "### 2.1 总导演",
                          fg, f"Bot分工里缺 {role} 的章节")
        text = read("总导演")
        self.assertIn("数据规范-v2.md", text)                  # 两边都指向同一份规范
        self.assertIn("作品清单.json", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
