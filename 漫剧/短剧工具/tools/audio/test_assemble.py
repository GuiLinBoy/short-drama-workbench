#!/usr/bin/env python3
"""assemble.py 的离线测试：ffmpeg 生成小测试片段，走完整合成链。运行：python -I test_assemble.py"""
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "assemble.py"
TOOLS = HERE.parent
sys.path.insert(0, str(TOOLS))
import make_sample_project  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8")

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
FONT_OK = Path(r"D:\fonts\SourceHanSansSC-Regular.otf").exists()


def make_clip(path, seconds, tone_freq="200"):
    """生成一个带音轨的测试片段：彩条画面 + 正弦音。"""
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc=size=320x568:rate=30:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency={tone_freq}:duration={seconds}",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(path)], check=True, capture_output=True)


class AssembleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAS_FFMPEG:
            raise unittest.SkipTest("ffmpeg/ffprobe 不在 PATH")
        cls.tmp = Path(tempfile.mkdtemp())
        cls.music = cls.tmp / "bgm.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
                        str(cls.music)], check=True, capture_output=True)

    def setUp(self):
        self.root = make_sample_project.build(self.tmp / f"p{id(self):x}" / "样例作品")
        (self.root / "approvals").mkdir(exist_ok=True)
        clips = json.loads((self.root / "clips.json").read_text(encoding="utf-8"))
        for c in clips:
            v = self.root / c["video"]
            v.parent.mkdir(parents=True, exist_ok=True)
            make_clip(v, c["duration"])
            c["status"] = "approved"
        (self.root / "clips.json").write_text(json.dumps(clips, ensure_ascii=False, indent=2), encoding="utf-8")

    def cli(self, *args, ok=True, code=None):
        if "--music" in args and "--speech-timeline" not in args and "--srt" not in args:
            timeline = self.root / "mock-speech.json"
            timeline.write_text(json.dumps({"segments": [[1,3]], "source": "manual-reviewed"}), encoding="utf-8")
            args = (*args, "--speech-timeline", "mock-speech.json")
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)],
                           capture_output=True, text=True, encoding="utf-8",
                           env={**os.environ, "PYTHONUTF8": "1"})
        out = r.stdout + r.stderr
        self.assertNotRegex(out, r"[A-Za-z0-9+/]{500,}")
        if code is not None:
            self.assertEqual(r.returncode, code, out)
        elif ok:
            self.assertEqual(r.returncode, 0, out)
        else:
            self.assertNotEqual(r.returncode, 0, out)
        return r

    def clips(self):
        return json.loads((self.root / "clips.json").read_text(encoding="utf-8"))

    def add_narration(self, dur=1.5):
        cs = self.clips()
        wav_dir = self.root / "单集制作" / "EP001" / "旁白"
        wav_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=600:duration={dur}",
                        str(wav_dir / "Clip00001-1.wav")], check=True, capture_output=True)
        cs[0]["narration"] = [{"id": "Clip00001-1", "start": 1, "reserved": 2, "text": "那一夜，雪下得很大。",
                               "audio": "单集制作/EP001/旁白/Clip00001-1.wav", "narration_duration": dur}]
        (self.root / "clips.json").write_text(json.dumps(cs, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_full_chain_with_music_narration_and_label(self):
        self.add_narration()
        out = self.root / "单集制作" / "EP001" / "成片.mp4"
        self.cli("--project", self.root, "--episode", "EP001", "--music", str(self.music), "--out", out)
        self.assertTrue(out.is_file())
        rep = json.loads((out.with_suffix(".validation.json")).read_text(encoding="utf-8"))
        self.assertEqual(rep["full_decode"], "passed")
        self.assertEqual(rep["ratio"], "9:16")
        self.assertEqual((rep["width"], rep["height"]), (720, 1280))       # M5：按 ratio 定画幅
        self.assertEqual(len(rep["clips"]), len(self.clips()))
        self.assertIn("narration", json.dumps(rep["steps"]["narration_music"]))
        self.assertTrue(rep["aigc_metadata"])
        self.assertFalse(rep["visual_review"] == "passed")
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(out)], capture_output=True, text=True)
        expect = sum(c["duration"] for c in self.clips())
        self.assertAlmostEqual(float(probe.stdout), expect, delta=0.5)
        srt = self.root / "单集制作" / "EP001" / "成片.srt"
        self.assertTrue(srt.is_file())                                     # 旁白字幕自动写 SRT
        self.assertIn("那一夜，雪下得很大。", srt.read_text(encoding="utf-8"))

    @unittest.skipUnless(FONT_OK, "思源黑体不在 D:\\fonts")
    def test_burn_subtitles_and_label(self):
        self.add_narration()
        out = self.root / "单集制作" / "EP001" / "成片-v2.mp4"
        self.cli("--project", self.root, "--episode", "EP001", "--out", out, "--burn-subtitles", "--overwrite")
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                                "stream=width,height", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
        self.assertEqual(tuple(int(x) for x in probe.stdout.strip().split(",")), (720, 1280))

    def test_rejects_unapproved_clips(self):
        cs = self.clips()
        cs[1]["status"] = "generated"
        (self.root / "clips.json").write_text(json.dumps(cs, ensure_ascii=False), encoding="utf-8")
        r = self.cli("--project", self.root, "--episode", "EP001", ok=False)
        self.assertIn("approved", r.stderr)

    def test_rejects_missing_narration_audio(self):
        cs = self.clips()
        cs[0]["narration"] = [{"id": "Clip00001-1", "start": 0, "reserved": 2, "text": "无音频"}]
        (self.root / "clips.json").write_text(json.dumps(cs, ensure_ascii=False), encoding="utf-8")
        r = self.cli("--project", self.root, "--episode", "EP001", ok=False)
        self.assertIn("还没配音", r.stderr)

    def test_rejects_existing_output_without_overwrite(self):
        out = self.root / "单集制作" / "EP001" / "成片.mp4"
        self.cli("--project", self.root, "--episode", "EP001", "--out", out)
        r = self.cli("--project", self.root, "--episode", "EP001", "--out", out, ok=False)
        self.assertIn("--overwrite", r.stderr)

    def test_license_warning_for_unregistered_music(self):
        out = self.root / "单集制作" / "EP001" / "成片.mp4"
        r = self.cli("--project", self.root, "--episode", "EP001", "--music", str(self.music), "--out", out)
        self.assertIn("版权未标", r.stderr)                                # 曲库.json 里没有这首曲子

    def test_final_from_draft_accepted(self):
        cs = self.clips()
        cs[1]["status"] = "planned"
        cs[1]["final_from_draft"] = True
        cs[1]["draft"] = {**cs[1].get("draft", {}), "status": "approved"}
        (self.root / "clips.json").write_text(json.dumps(cs, ensure_ascii=False), encoding="utf-8")
        out = self.root / "单集制作" / "EP001" / "成片.mp4"
        self.cli("--project", self.root, "--episode", "EP001", "--out", out)
        rep = json.loads((out.with_suffix(".validation.json")).read_text(encoding="utf-8"))
        self.assertTrue(rep["clips"][1]["from_draft"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
