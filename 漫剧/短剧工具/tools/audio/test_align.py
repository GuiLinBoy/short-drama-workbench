#!/usr/bin/env python3
"""align.py 的离线测试：用桩 FunASR 环境（输出固定识别结果），不加载真模型。运行：python -I test_align.py"""
import json, os, shutil, struct, subprocess, sys, tempfile, unittest, wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "align.py"
TOOLS = HERE.parent
sys.path.insert(0, str(TOOLS))
import make_sample_project  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8")

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

FAKE_TEXT = "那一夜，山门外的雪下得很大。林舟握紧了剑。"
FAKE_NCHARS = sum(1 for ch in FAKE_TEXT if ch.isalnum())            # 18
FAKE_RESULT = {
    "text": FAKE_TEXT,
    "timestamp": [[800 + i * 300, 800 + i * 300 + 250] for i in range(FAKE_NCHARS)],  # 首字 0.8s，每字 0.3s
    "speech_segments": [[0.7, 5.6]],
}

# 桩 ASR 环境：一个 python.exe（用系统 python 模拟），跑 -I <src> <wav> <out> <models> 时写固定 JSON
FAKE_ASR_DIR_NAME = "asr-fake"


def make_fake_asr(tmp: Path) -> Path:
    d = tmp / FAKE_ASR_DIR_NAME
    d.mkdir()
    (d / "Scripts").mkdir()
    if os.name == "nt":
        exe = d / "Scripts" / "python.exe"
        exe.write_text("", encoding="utf-8")                       # 占位，存在性检查用
        real = shutil.which("python") or sys.executable
        exe.write_bytes(b"")
        # Windows 没法让一个假 exe 真的执行；改成写一个 .bat 桥接到真 python
        bat = d / "Scripts" / "python.bat"
        bat.write_text(f'@\"{sys.executable}\" %*\n', encoding="ascii")
        return exe                                                  # align.py 只检查存在性
    return d / "Scripts" / "python"


def write_wav(path: Path, seconds=1.0, rate=16000):
    n = int(rate * seconds)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(b"\x00\x00" * n)


# align.py 的 recognize 子进程直接执行 ASR_PY <src> <out> <models>。
# 桩：ASR_PY 指到当前 python，src 是 align.py 生成的 RECOGNIZE_SRC——没法让真 python 跑出桩结果。
# 所以测试通过环境变量给 align.py 一个"桩模式"：ASR_FAKE_RESULT 指向固定结果文件时直接用它。
# 这个钩子在 align.py 里只有测试会设置，生产路径不受影响。


class AlignTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.fake_result = cls.tmp / "fake_asr_result.json"
        cls.fake_result.write_text(json.dumps(FAKE_RESULT), encoding="utf-8")
        cls.asr_py = make_fake_asr(cls.tmp)

    def setUp(self):
        self.root = make_sample_project.build(self.tmp / f"proj{id(self):x}" / "样例作品")
        self.env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                    "ASR_PY": str(self.asr_py), "ASR_FAKE_RESULT": str(self.fake_result)}

    def cli(self, *args, ok=True, code=None):
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)],
                           capture_output=True, text=True, encoding="utf-8", env=self.env)
        if code is not None:
            self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        elif ok:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        else:
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    # ---- sentences_from_timestamp（纯逻辑，直接测）
    def test_sentences_split_by_punctuation(self):
        sys.path.insert(0, str(HERE))
        import align
        sents = align.sentences_from_timestamp(FAKE_RESULT["timestamp"], FAKE_RESULT["text"])
        self.assertEqual([s["text"] for s in sents], ["那一夜", "山门外的雪下得很大", "林舟握紧了剑"])
        self.assertEqual(sents[0]["start"], 0.8)
        self.assertAlmostEqual(sents[-1]["end"], (800 + FAKE_NCHARS * 300 - 50) / 1000)

    # ---- align（桩识别）
    def test_align_writes_result(self):
        out = self.tmp / "align_out.json"
        r = json.loads(self.cli("align", "nonexistent.wav", "--out", out).stdout)
        self.assertEqual(r["chars"], FAKE_NCHARS)
        self.assertEqual(r["sentences"], 3)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["speech_segments"], [[0.7, 5.6]])

    def test_align_needs_asr_env(self):
        env = {k: v for k, v in self.env.items() if k not in ("ASR_PY", "ASR_FAKE_RESULT")}
        env["ASR_PY"] = str(self.tmp / "missing-asr-python.exe")
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "align", "x.wav"], capture_output=True,
                           text=True, encoding="utf-8", env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("FunASR", r.stderr)

    def test_align_preserves_existing_output(self):
        output=self.root/'already-reviewed.json';output.write_bytes(b'preserve original bytes')
        self.cli('align','source.wav','--out',output,ok=False)
        self.assertEqual(output.read_bytes(),b'preserve original bytes')

    # ---- check 乱说话质检
    def test_check_clean_passes(self):
        r = json.loads(self.cli("check", "a.wav", "--dialogue", "那一夜，山门外的雪下得很大。",
                                "--dialogue", "林舟握紧了剑。").stdout)
        self.assertTrue(r["ok"])
        self.assertEqual(r["problems"], [])

    def test_check_flags_leading_speech(self):
        p = self.tmp / "lead.json"
        p.write_text(json.dumps({**FAKE_RESULT, "timestamp": [[i * 300, i * 300 + 250] for i in range(FAKE_NCHARS)]}),
                     encoding="utf-8")                           # 首字 0s：贴着片段开头
        env = {**self.env, "ASR_FAKE_RESULT": str(p)}
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "check", "a.wav", "--dialogue", "那一夜山门外的雪下得很大林舟握紧了剑"],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("疑似乱说话开头", r.stdout)

    def test_check_flags_extra_speech(self):
        p = self.tmp / "extra.json"
        p.write_text(json.dumps({**FAKE_RESULT, "text": FAKE_RESULT["text"] + "今天天气真好啊。"}),
                     encoding="utf-8")
        env = {**self.env, "ASR_FAKE_RESULT": str(p)}
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "check", "a.wav",
                            "--dialogue", "那一夜山门外的雪下得很大林舟握紧了剑"],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("台词以外", r.stdout)

    # ---- cut-voice
    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe 不在 PATH")
    def test_cut_voice_trims_registered_range(self):
        voices = [{"character": "C00001", "file": "音色/C00001.wav", "source_clip": "EP001/Clip00001",
                   "start": 0.0, "end": 4.0, "text": "哼", "status": "generated", "forms": ["C00001-A"]}]
        (self.root / "voices.json").write_text(json.dumps(voices, ensure_ascii=False), encoding="utf-8")
        src = self.tmp / "defining.mp4"
        write_wav(self.tmp / "defining.wav", 6.0)
        src = self.tmp / "defining.wav"
        out = self.root / "音色" / "C00001.wav"
        r = json.loads(self.cli("cut-voice", "--project", self.root, "--character", "C00001",
                                "--source", src).stdout)
        self.assertEqual(r["duration"], 4.0)
        self.assertTrue(out.is_file())

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe 不在 PATH")
    def test_cut_voice_rejects_bad_status_and_range(self):
        voices = [{"character": "C00001", "file": "音色/C00001.wav", "source_clip": "EP001/Clip00001",
                   "start": 0.0, "end": 8.0, "text": "哼", "status": "planned", "forms": ["C00001-A"]}]
        (self.root / "voices.json").write_text(json.dumps(voices, ensure_ascii=False), encoding="utf-8")
        src = self.tmp / "x.wav"
        write_wav(src, 9.0)
        r = self.cli("cut-voice", "--project", self.root, "--character", "C00001", "--source", src, ok=False)
        self.assertIn("generated/approved", r.stderr)
        voices[0]["status"] = "generated"
        (self.root / "voices.json").write_text(json.dumps(voices, ensure_ascii=False), encoding="utf-8")
        r = self.cli("cut-voice", "--project", self.root, "--character", "C00001", "--source", src)
        self.assertIn("不在 H3 建议", r.stderr)                       # 8s 超出 3–5s，只警告不拦

    # ---- subtitle
    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe 不在 PATH")
    def test_cut_voice_before_registration_and_no_clobber(self):
        src=self.root/'raw.wav'
        write_wav(src,5)
        voices=self.root/'voices.json'
        original=voices.read_bytes() if voices.exists() else None
        args=("cut-voice","--project",self.root,"--character","C00001","--source",src,
              "--start",".5","--end","3.9","--out","音色/first.wav")
        data=json.loads(self.cli(*args).stdout)
        self.assertTrue(data['registration_required'])
        self.assertAlmostEqual(data['duration'],3.4)
        self.assertEqual(voices.read_bytes() if voices.exists() else None,original)
        output=self.root/'音色/first.wav';before=output.read_bytes()
        self.assertIn('已存在',self.cli(*args,ok=False).stderr)
        self.assertEqual(before,output.read_bytes())

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe 不在 PATH")
    def test_cut_voice_refuses_escape_partial_and_invalid_ranges(self):
        src=self.root/'raw.wav';write_wav(src,5)
        args=("cut-voice","--project",self.root,"--character","C00001","--source",src)
        for start,end,out in [('0','4','../escape.wav'),('4','2','音色/bad.wav'),
                              ('nan','4','音色/bad.wav'),('0','6','音色/bad.wav'),
                              ('0','4','音色/bad.mp3')]:
            self.cli(*args,'--start',start,'--end',end,'--out',out,ok=False)
        self.cli(*args,'--start','0',ok=False)
        self.assertFalse((self.root/'音色/bad.wav').exists())
        self.assertFalse((self.root.parent/'escape.wav').exists())

    def test_subtitle_uses_script_text_with_recognized_timing(self):
        script = self.tmp / "script.txt"
        script.write_text("那一夜，山门外的雪下得很大。\n林舟握紧了剑。", encoding="utf-8")
        out = self.tmp / "成片.srt"
        r = json.loads(self.cli("subtitle", "a.wav", "--script", script, "--out", out).stdout)
        self.assertEqual(r["lines"], 2)
        text = out.read_text(encoding="utf-8")
        self.assertIn("-->", text)
        self.assertIn("那一夜，山门外的雪下得很大。", text)          # 文字来自剧本（带标点）
        self.assertIn("林舟握紧了剑。", text)
        self.assertNotIn("握紧了剑", text.split("林舟")[0])          # 顺序没乱

    def test_subtitle_refuses_script_mismatch_without_output(self):
        script = self.tmp / "s2.txt"
        script.write_text("完全不同的台词。", encoding="utf-8")
        out = self.tmp / "mismatch.srt"
        r = self.cli("subtitle", "a.wav", "--script", script, "--out", out, ok=False)
        self.assertIn("不匹配", r.stderr)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
