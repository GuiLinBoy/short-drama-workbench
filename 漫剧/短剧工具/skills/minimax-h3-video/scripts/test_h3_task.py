#!/usr/bin/env python3
"""Offline tests for h3_task.py against a local mock of the H3 API. Run: python3 -I test_h3_task.py"""
import json, os, shutil, struct, subprocess, sys, tempfile, threading, time, unittest, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).with_name("h3_task.py")
KEY = "sk-ml-TEST-SECRET-123"
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def tiny_png(path, rgb=(200, 30, 30), size=32):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    row = b"\x00" + bytes(rgb) * size
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b""))


class Mock(BaseHTTPRequestHandler):
    calls, by_key, mode = [], {}, "ok"
    video = b""
    balance = 1000

    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Mock.calls.append(("POST", self.path, body, dict(self.headers)))
        if Mock.mode == "500":
            return self._send(502, {"error": "upstream"})
        if Mock.mode == "400":
            return self._send(400, {"type": "error", "error": {"type": "bad_request_error", "message": "bad"}})
        key = self.headers.get("Idempotency-Key")
        tid = Mock.by_key.setdefault(key, f"task-{len(Mock.by_key) + 1}")
        self._send(200, {"task_id": tid})

    def do_GET(self):
        Mock.calls.append(("GET", self.path, None, dict(self.headers)))
        if self.path == "/video.mp4":
            return self._send(200, raw=Mock.video, ctype="video/mp4")
        if self.path.startswith("/minimax/v2/query/video_generation/"):
            tid = self.path.rsplit("/", 1)[1]
            status = {"running": "running", "failed": "failed", "blocked": "blocked"}.get(Mock.mode, "succeeded")
            task = {"id": tid, "status": status, "point_status": "released" if status in ("failed", "blocked") else "consumed", "resolution": "480P", "estimated_remaining_seconds": 0,
                    "usage": {"output_seconds": 4}}
            if status == "succeeded":
                task["content"] = {"url": f"http://127.0.0.1:{self.server.server_port}/video.mp4"}
            return self._send(200, {"task": task})
        if self.path == "/minimax/v2/query/point_usage_summary":
            if Mock.mode == "balfail":
                return self._send(500, {"error": "upstream"})
            return self._send(200, {"total_points": Mock.balance, "reserved_points": 0,
                                    "available_points": Mock.balance})
        self._send(404, {"error": "nf"})


class H3TaskTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tmp = Path(tempfile.mkdtemp())
        if HAS_FFMPEG:
            mp4 = cls.tmp / "v.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                            "-pix_fmt", "yuv420p", str(mp4)], check=True)
            Mock.video = mp4.read_bytes()
        for i, name in enumerate(("tail.png", "c1.png", "s1.png", "c2.png")):
            tiny_png(cls.tmp / name, (40 * i, 30, 30))

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Mock.calls.clear(); Mock.by_key.clear(); Mock.mode = "ok"
        Mock.balance = 1000
        self.manju = Path(tempfile.mkdtemp(dir=self.tmp)) / "漫剧"
        self.d = self.manju / "作品" / "样例作品"          # self.d plays the project dir
        self.d.mkdir(parents=True)
        for name in ("budget.json",):
            (self.d / "approvals").mkdir(exist_ok=True)

    def settings(self, **h3):
        f = self.manju / "全局设置.json"
        s = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"h3": {}, "nexorai": {}}
        s["h3"].update(h3)
        f.write_text(json.dumps(s), encoding="utf-8")

    def ledger(self):
        f = self.manju / "全局账本.jsonl"
        return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []

    def run_cli(self, *args, ok=True, code=None):
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "MINIMAX_H3_API_KEY": KEY, "H3_BASE_URL": f"http://127.0.0.1:{self.srv.server_port}"}
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)], capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertNotIn(KEY, r.stdout + r.stderr, "API key leaked to output")
        if code is not None:
            self.assertEqual(r.returncode, code, r.stderr)
        elif ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        else:
            self.assertNotEqual(r.returncode, 0, r.stderr)
        return r

    def budget_file(self):
        f = self.d / "approvals" / "budget.json"   # self.d plays the project directory
        f.parent.mkdir(exist_ok=True)
        return f

    def budget_args(self, limit=1000, batch="EP001-试生成"):
        f = self.budget_file()
        if not f.exists():
            f.write_text(json.dumps([{"batch": batch, "kind": "video", "unit": "points", "limit": limit, "used": 0}]), encoding="utf-8")
        return ["--budget", f, "--batch", batch]

    def used(self):
        return json.loads(self.budget_file().read_text(encoding="utf-8"))[0]["used"]

    def prompt(self, text):
        p = self.d / "p.txt"; p.write_text(text, encoding="utf-8"); return p

    def prepare(self, text, *refs, extra=(), ok=True, name="req.json"):
        args = ["prepare", "--prompt", self.prompt(text), "--output", self.d / name, "--duration", "6", "--ratio", "9:16"]
        # These accounting fixtures intentionally exercise 480P with Context-IR;
        # do not accidentally inherit a changed production default.
        if "--resolution" not in extra:
            args += ["--resolution", "480P"]
        if "--context-ir" not in extra:
            args += ["--context-ir", "on"]
        for r in refs:
            args += ["--ref", r]
        return self.run_cli(*args, *extra, ok=ok)

    # ---- prepare
    def test_prepare_default_resolution_is_native_768p(self):
        self.run_cli("prepare", "--prompt", self.prompt("No one speaks; a fixed room."),
                     "--output", self.d / "default.json", "--duration", "6")
        request = json.loads((self.d / "default.json").read_text(encoding="utf-8"))
        self.assertEqual(request["params"]["resolution"], "768P")
        self.assertFalse(request["params"]["use_context_ir"])
        self.assertEqual(request["billing"]["context_ir_points"], 0)
        self.assertEqual(request["billing"]["output_points"], 60)
        self.assertEqual(request["estimate_points"], 60)

    def test_explicit_context_ir_opt_in_is_preserved(self):
        self.run_cli("prepare", "--prompt", self.prompt("No one speaks; a fixed room."),
                     "--output", self.d / "explicit-ir.json", "--duration", "6", "--context-ir", "on")
        request = json.loads((self.d / "explicit-ir.json").read_text(encoding="utf-8"))
        self.assertTrue(request["params"]["use_context_ir"])
        self.assertEqual(request["billing"]["context_ir_points"], 5)
        self.assertEqual(request["estimate_points"], 65)

    def test_label_mapping_in_attachment_order(self):
        r = self.prepare("[Clip00001-尾帧]延续：[C00001-A]转身看向[S00001]深处。",
                         f"Clip00001-尾帧={self.tmp/'tail.png'}", f"C00001-A={self.tmp/'c1.png'}", f"S00001={self.tmp/'s1.png'}")
        req = json.loads((self.d / "req.json").read_text(encoding="utf-8"))
        self.assertEqual(req["prompt"], "<Picture 1>延续：<Picture 2>转身看向<Picture 3>深处。")
        self.assertEqual(req["estimate_points"], 35)  # 6*5 output + 5 Context-IR
        self.assertEqual(json.loads(r.stdout)["mapping"]["C00001-A"], "<Picture 2>")

    def test_first_frame_keyframe_label(self):
        self.prepare("For the target video, at 0.00 seconds into the target video, [K00012] (from [Shot 1]) is fully referenced.",
                     f"K00012={self.tmp/'s1.png'}@first_frame")
        req = json.loads((self.d / "req.json").read_text(encoding="utf-8"))
        self.assertTrue(req["prompt"].startswith("For the target video, at 0.00 seconds into the target video, <Picture 1>"))
        self.assertEqual(req["references"][0]["role"], "first_frame")

    def test_audio_reference_numbered_separately(self):
        wav = self.tmp / "v.wav"; wav.write_bytes(b"RIFF0000WAVEfmt ")
        self.prepare("[C00001-A] ... [C00001-A音色参考] is the voice-timbre reference.",
                     f"C00001-A={self.tmp/'c1.png'}", f"C00001-A音色参考={wav}@reference_audio", name="a.json")
        req = json.loads((self.d / "a.json").read_text(encoding="utf-8"))
        self.assertIn("<Picture 1> ... <Audio 1> is", req["prompt"])

    def test_dialogue_symbol_warnings(self):
        r = self.prepare("[C00001-A] says: <d>[Chinese] 你……骗我~（冷笑）真的吗？！</d> then <d>[Chinese] 呵，三年。你骗了我，整整三年？</d>",
                         f"C00001-A={self.tmp/'c1.png'}", name="d.json")
        w = " | ".join(json.loads(r.stdout)["warnings"])
        for key in ("dialogue 1: ellipsis", "dialogue 1: tilde", "dialogue 1: repeated", "dialogue 1: parenthesized"):
            self.assertIn(key, w)
        self.assertNotIn("dialogue 2", w)
        r = self.prepare("[C00001-A] says: <d>[Chinese] 你来了</d>", f"C00001-A={self.tmp/'c1.png'}", name="e.json")
        self.assertIn("does not end", " ".join(json.loads(r.stdout)["warnings"]))

    def test_narrator_warns(self):
        r = self.prepare("[C00001-A] walks. A narrator (S2) says in an off-screen voiceover: <d>[Chinese] 那一年，他十七岁。</d>",
                         f"C00001-A={self.tmp/'c1.png'}", name="n.json")
        self.assertIn("narrator found", " ".join(json.loads(r.stdout)["warnings"]))

    def test_execute_requires_budget(self):
        req, task = self._ready()
        r = self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", ok=False)
        self.assertIn("no unbudgeted submits", r.stderr)
        self.assertEqual([c for c in Mock.calls if c[0] == "POST"], [])

    def test_budget_charged_refused_and_refunded(self):
        req, task = self._ready()  # 6 s at 480P + Context-IR = 35 points
        self.budget_file().write_text(json.dumps([{"batch": "EP001-试生成", "kind": "video", "unit": "points", "limit": 40, "used": 0}]), encoding="utf-8")
        self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args())
        self.assertEqual(self.used(), 35)
        r = self.run_cli("submit", "--request", req, "--task-dir", task.parent / "attempt-002", "--execute", *self.budget_args(), ok=False)
        self.assertIn("budget exceeded", r.stderr)
        self.assertEqual(len([c for c in Mock.calls if c[0] == "POST"]), 1)
        Mock.mode = "blocked"
        self.run_cli("query", "--task-dir", task)
        self.run_cli("query", "--task-dir", task)  # refund happens once
        self.assertEqual(self.used(), 0)

    def test_rejected_submit_is_refunded(self):
        req, task = self._ready()
        Mock.mode = "400"
        self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args(), ok=False)
        self.assertEqual(self.used(), 0)

    def test_execution_policy_blocks_before_budget_and_provider_post(self):
        req,task=self._ready()
        cap={'version':1,'caps':{'total_points':600,'h3_requests':0,'tts_requests':5,'tts_characters':300,
              'images':6,'video_revisions':2,'revisions_per_clip':1},'allowed_resolutions':['480P','768P'],
              'retry_unknown':False,'revision_batch':'EP001-重做'}
        (self.d/'approvals/execution-limits.json').write_text(json.dumps(cap),encoding='utf-8')
        self.run_cli('submit','--request',req,'--task-dir',task,'--execute',*self.budget_args(),ok=False)
        self.assertEqual(self.used(),0)
        self.assertFalse((task/'state.json').exists())
        self.assertEqual([c for c in Mock.calls if c[0]=='POST'],[])

    def test_continuity_markers(self):
        body = ("subject_definitions:\n[Clip00001-尾帧] is the first frame of [Shot 1], showing {{承接定义：expected: he stands on the right}}.\n"
                "[C00001-A] ...\ndetailed_description:\n[Shot 1] The shot begins from [Clip00001-尾帧]: {{承接开场：expected opening}} He draws.")
        refs = (f"Clip00001-尾帧={self.tmp/'tail.png'}", f"C00001-A={self.tmp/'c1.png'}")
        r = self.prepare(body, *refs, name="c1.json")
        self.assertIn("not verified", " ".join(json.loads(r.stdout)["warnings"]))
        self.assertNotIn("{{", json.loads((self.d / "c1.json").read_text(encoding="utf-8"))["prompt"])
        p = self.d / "p.txt"
        r = self.run_cli("set-continuity", "--prompt", p, "--frame", self.tmp / "tail.png", "--opening", "<d>[Chinese] 你好。</d>", ok=False)
        self.assertIn("no dialogue", r.stderr)
        self.run_cli("set-continuity", "--prompt", p, "--frame", self.tmp / "tail.png",
                     "--definition", "[C00001-A] standing on the left third, facing right",
                     "--opening", "[C00001-A] stands on the left third, sword lowered, facing right.")
        text = p.read_text(encoding="utf-8")
        self.assertIn("{{承接开场·已核对：", text)
        self.assertIn(" He draws.", text)  # nothing outside the markers changed
        self.assertNotIn(b"\r\n", p.read_bytes())  # line endings stay LF on Windows too
        self.assertTrue((self.d / "p.continuity.jsonl").exists())
        args = ["prepare", "--prompt", p, "--output", self.d / "c2.json", "--duration", "6", "--ratio", "9:16"]
        for x in refs:
            args += ["--ref", x]
        r = self.run_cli(*args)
        self.assertNotIn("not verified", " ".join(json.loads(r.stdout)["warnings"]))

    def test_silence_warnings(self):
        r = self.prepare("[C00001-A] walks to the window.\n\noverall_soundscape: N/A", f"C00001-A={self.tmp/'c1.png'}", name="s1.json")
        w = " | ".join(json.loads(r.stdout)["warnings"])
        self.assertIn("may invent speech", w)
        self.assertIn("overall_soundscape missing", w)
        r = self.prepare("[C00001-A] silently walks to the window.\n\noverall_soundscape: Rain taps on the roof tiles.\n\nnon_diegetic_music: N/A",
                         f"C00001-A={self.tmp/'c1.png'}", name="s2.json")
        w = " | ".join(json.loads(r.stdout)["warnings"])
        self.assertNotIn("may invent speech", w)
        self.assertNotIn("overall_soundscape", w)

    def test_chinese_prompt_is_preserved_and_explicit_silence_is_recognized(self):
        body = ("subject_definitions:\n<Subject 1> 是[C00001-A]中的归人。\n\n"
                "summary:\n归人站在雪夜门前。\n\nretention_analysis:\n<Subject 1>: fully_preserved - 保留人物外貌。\n\n"
                "detailed_description:\n[Shot 1] 归人缓慢抬掌，本镜头全程无人说话，人物始终双唇闭合。不出现字幕或画面文字。禁止生成任何旁白。\n\n"
                "overall_soundscape:\n寒风掠过门缝，衣料轻轻摩擦。\n\nnon_diegetic_music:\nN/A")
        result = self.prepare(body, f"C00001-A={self.tmp/'c1.png'}", name='zh.json')
        request = json.loads((self.d/'zh.json').read_text('utf-8'))
        self.assertEqual(request['prompt'], body.replace('[C00001-A]', '<Picture 1>'))
        self.assertEqual(json.loads(result.stdout)['warnings'], [])
        without_silence = body.replace('本镜头全程无人说话，人物始终双唇闭合。', '')
        result = self.prepare(without_silence, f"C00001-A={self.tmp/'c1.png'}", name='zh-missing.json')
        self.assertIn('may invent speech', ' '.join(json.loads(result.stdout)['warnings']))
        result = self.prepare(body+'\n旁白说：他终于回家了。', f"C00001-A={self.tmp/'c1.png'}", name='zh-narrator.json')
        self.assertIn('narrator found', ' '.join(json.loads(result.stdout)['warnings']))

    def test_chinese_continuity_keeps_actual_frame_evidence_and_surrounding_text(self):
        body = ('subject_definitions:\n[Clip00001-尾帧]显示{{承接定义：待核对}}。\n\n'
                'detailed_description:\n[Shot 1] {{承接开场：待核对}} 后续动作保持原稿。')
        prompt = self.prompt(body)
        self.run_cli('set-continuity','--prompt',prompt,'--frame',self.tmp/'tail.png',
                     '--definition','[C00001-A]在画面右侧，面向闭合木门，右掌停在木面前。',
                     '--opening','从真实尾帧的站姿、门扇状态和冷暖光线开始，全程无人说话。')
        rewritten = prompt.read_text('utf-8')
        self.assertIn('后续动作保持原稿。', rewritten)
        self.assertIn('{{承接开场·已核对：从真实尾帧', rewritten)
        for name, frame in [('same', 'tail.png'), ('changed', 's1.png')]:
            result = self.run_cli('prepare','--prompt',prompt,'--output',self.d/(name+'.json'),
                                  '--duration','8','--ref','C00001-A='+str(self.tmp/'c1.png'),
                                  '--ref','Clip00001-尾帧='+str(self.tmp/frame))
            warnings = ' '.join(json.loads(result.stdout)['warnings'])
            self.assertEqual('different tail frame' in warnings, name=='changed')

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe not installed")
    def test_long_reference_audio_warns(self):
        wav = self.d / "long.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "8", str(wav)], check=True)
        r = self.prepare("[C00001-A] says: <d>[Chinese] 你来了。</d> He closes his lips.\n\noverall_soundscape: Wind.",
                         f"C00001-A={self.tmp/'c1.png'}", f"C00001音色参考={wav}@reference_audio", name="a8.json")
        self.assertIn("keep voice templates around 4 s", " ".join(json.loads(r.stdout)["warnings"]))

    def test_rejects_hardcoded_numbers(self):
        r = self.prepare("图1 的人物走向镜头", f"C00001-A={self.tmp/'c1.png'}", ok=False)
        self.assertIn("fixed numbers", r.stderr)
        r = self.prepare("<Picture 1> walks", f"C00001-A={self.tmp/'c1.png'}", ok=False, name="b.json")
        self.assertIn("fixed numbers", r.stderr)

    def test_rejects_frame_and_reference_mix(self):
        r = self.prepare("[C00001-A]走", f"Clip00001-尾帧={self.tmp/'tail.png'}@first_frame",
                         f"C00001-A={self.tmp/'c1.png'}", ok=False)
        self.assertIn("cannot mix", r.stderr)

    def test_rejects_missing_label_and_long_without_ir(self):
        r = self.prepare("[C00002-A]出现", f"C00001-A={self.tmp/'c1.png'}", ok=False)
        self.assertIn("not attached", r.stderr)
        r = self.run_cli("prepare", "--prompt", self.prompt("走"), "--output", self.d / "x.json", "--duration", "20",
                         "--context-ir", "off", ok=False)
        self.assertIn("requires --context-ir on", r.stderr)

    def test_too_many_images(self):
        refs = [f"[R{i}参考]={self.tmp/'c1.png'}" for i in range(10)]
        r = self.prepare("走", *refs, ok=False)
        self.assertIn("limits exceeded", r.stderr)

    def test_unused_reference_warns(self):
        r = self.prepare("[C00001-A]走", f"C00001-A={self.tmp/'c1.png'}", f"C00002-A={self.tmp/'c2.png'}")
        self.assertTrue(json.loads(r.stdout)["warnings"])

    # ---- submit
    def _ready(self):
        self.prepare("[C00001-A]走向镜头", f"C00001-A={self.tmp/'c1.png'}")
        return self.d / "req.json", self.d / "tasks" / "EP001-Clip00001-draft" / "attempt-001"

    def test_preview_makes_no_network_call(self):
        req, task = self._ready()
        r = self.run_cli("submit", "--request", req, "--task-dir", task)
        self.assertTrue(json.loads(r.stdout)["preview"])
        self.assertEqual(Mock.calls, [])
        self.assertFalse(task.exists())

    def test_execute_once_then_locked(self):
        req, task = self._ready()
        self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args())
        state = json.loads((task / "state.json").read_text(encoding="utf-8"))
        self.assertEqual((state["status"], state["task_id"]), ("submitted", "task-1"))
        post = [c for c in Mock.calls if c[0] == "POST"][0]
        body = post[2]
        self.assertEqual(body["content"][1]["role"], "reference_image")
        self.assertTrue(body["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertTrue(post[3].get("Idempotency-Key"))
        r = self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args(), ok=False)
        self.assertIn("already reserved", r.stderr)
        self.assertEqual(len([c for c in Mock.calls if c[0] == "POST"]), 1)
        self.assertNotIn("base64", (task / "request.json").read_text(encoding="utf-8"))

    def test_unknown_then_retry_reuses_idempotency_key(self):
        req, task = self._ready()
        Mock.mode = "500"
        r = self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args(), ok=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(json.loads((task / "state.json").read_text(encoding="utf-8"))["status"], "unknown")
        Mock.mode = "ok"
        self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", "--retry-unknown")
        keys = [c[3]["Idempotency-Key"] for c in Mock.calls if c[0] == "POST"]
        self.assertEqual(len(set(keys)), 1)
        self.assertEqual(json.loads((task / "state.json").read_text(encoding="utf-8"))["task_id"], "task-1")

    def test_budget_must_be_projects_own_file(self):
        req, task = self._ready()
        rogue = self.d / "elsewhere" / "budget.json"
        rogue.parent.mkdir()
        rogue.write_text(json.dumps([{"batch": "B", "kind": "video", "unit": "points", "limit": 99999, "used": 0}]), encoding="utf-8")
        r = self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", "--budget", rogue, "--batch", "B", ok=False)
        self.assertIn("approvals/budget.json", r.stderr)
        r = self.run_cli("submit", "--request", req, "--task-dir", self.d / "attempt-001", "--execute", *self.budget_args(), ok=False)
        self.assertIn("tasks/<task name>/attempt-NNN", r.stderr)
        self.assertEqual([c for c in Mock.calls if c[0] == "POST"], [])

    def test_new_attempt_is_a_real_reroll(self):
        req, task = self._ready()
        self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args())
        self.run_cli("submit", "--request", req, "--task-dir", task.parent / "attempt-002", "--execute", *self.budget_args())
        keys = [c[3]["Idempotency-Key"] for c in Mock.calls if c[0] == "POST"]
        self.assertEqual(len(set(keys)), 2)            # different key -> the platform makes a new task
        self.assertEqual(len(Mock.by_key), 2)

    def test_continuity_checked_against_other_frame_warns(self):
        body = ("subject_definitions:\n[Clip00001-尾帧] shows {{承接定义：expected}}.\n"
                "detailed_description:\n[Shot 1] {{承接开场：expected}} He draws.")
        p = self.prompt(body)
        self.run_cli("set-continuity", "--prompt", p, "--frame", self.tmp / "tail.png",
                     "--definition", "[C00001-A] on the left", "--opening", "[C00001-A] stands on the left.")
        base = ["prepare", "--prompt", p, "--duration", "6", "--ratio", "9:16", "--ref", f"C00001-A={self.tmp/'c1.png'}"]
        r = self.run_cli(*base, "--output", self.d / "same.json", "--ref", f"Clip00001-尾帧={self.tmp/'tail.png'}")
        self.assertNotIn("different tail frame", " ".join(json.loads(r.stdout)["warnings"]))
        r = self.run_cli(*base, "--output", self.d / "other.json", "--ref", f"Clip00001-尾帧={self.tmp/'s1.png'}")
        self.assertIn("different tail frame", " ".join(json.loads(r.stdout)["warnings"]))

    # ---- 全局层（第 3 组）：余额、并发、账本、pending/poll
    def test_insufficient_balance_rejects_without_charging(self):
        req, task = self._ready()
        Mock.balance = 20                                    # 预估 35，含 Context-IR
        r = self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args(), ok=False)
        self.assertIn("余额不够", r.stderr)
        self.assertEqual(self.used(), 0)
        self.assertEqual([c for c in Mock.calls if c[0] == "POST"], [])
        self.assertFalse((task / "submission.lock").exists())  # attempt 还能用

    def test_balance_check_failure_warns_but_submits(self):
        req, task = self._ready()
        Mock.mode = "balfail"
        self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args())
        self.assertEqual(self.used(), 35)                    # 照常提交（决定 2026-11-03）

    def test_concurrency_cap_queues_without_charging(self):
        self.settings(max_running_tasks=1)
        req1, task1 = self._ready()
        self.run_cli("submit", "--request", req1, "--task-dir", task1, "--execute", *self.budget_args())
        self.assertEqual(self.used(), 35)
        self.prepare("[C00001-A] 走向镜头", f"C00001-A={self.tmp/'c1.png'}", name="req-b.json")
        req2 = self.d / "req-b.json"
        task2 = self.d / "tasks" / "EP001-Clip00002-draft" / "attempt-001"
        r = self.run_cli("submit", "--request", req2, "--task-dir", task2, "--execute", *self.budget_args(), code=4)
        self.assertIn("排队中", r.stderr)
        self.assertEqual(self.used(), 35)                    # 没扣第二笔
        self.assertFalse((task2 / "submission.lock").exists())
        self.assertEqual(len([c for c in Mock.calls if c[0] == "POST"]), 1)
        # 第一个任务结束后同一个 attempt 可以直接再提交
        self.run_cli("query", "--task-dir", task1)           # -> succeeded, 释放并发名额
        out = json.loads(self.run_cli("submit", "--request", req2, "--task-dir", task2, "--execute", *self.budget_args()).stdout)
        self.assertEqual(out["status"], "submitted")

    def test_concurrent_submits_do_not_exceed_cap(self):
        self.settings(max_running_tasks=1)
        reqs, tasks = [], []
        for i in range(1, 4):
            self.prepare("[C00001-A] 走向镜头", f"C00001-A={self.tmp/'c1.png'}", name=f"p{i}.json")
            reqs.append(self.d / f"p{i}.json")
            tasks.append(self.d / "tasks" / f"EP001-Clip0000{i}-draft" / "attempt-001")
        procs = [subprocess.Popen(
            [sys.executable, "-I", str(SCRIPT), "submit", "--request", str(reqs[i]), "--task-dir", str(tasks[i]),
             "--execute", *self.budget_args()],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "PYTHONUTF8": "1", "MINIMAX_H3_API_KEY": KEY,
                 "H3_BASE_URL": f"http://127.0.0.1:{self.srv.server_port}"}) for i in range(3)]
        codes = [p.wait() for p in procs]
        self.assertEqual(codes.count(0), 1)                  # 只有一个提交成功
        self.assertEqual(codes.count(4), 2)                  # 其余排队
        self.assertEqual(len([c for c in Mock.calls if c[0] == "POST"]), 1)
        self.assertEqual(self.used(), 35)

    def test_ledger_charge_settle_and_refund(self):
        req, task = self._ready()
        self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args())
        events = [e["event"] for e in self.ledger()]
        self.assertEqual(events, ["charge"])
        self.assertEqual(self.ledger()[0]["provider_event"], "h3")
        self.run_cli("query", "--task-dir", task)            # 4*5 + 5 Context-IR = 25，比预估35少10
        settle = next(e for e in self.ledger() if e["event"] == "settle")
        self.assertEqual((settle["estimated_points"], settle["actual_points"], settle["adjusted"]), (35, 25, 10))
        self.assertEqual(self.used(), 25)                    # 按完整实际用量退回差额10
        st = json.loads((task / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(st["charge"]["settled"], True)

    def test_retry_unknown_skips_balance_and_concurrency(self):
        req, task = self._ready()
        self.settings(max_running_tasks=1)
        Mock.mode = "500"
        self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args(), ok=False)
        Mock.balance = 0                                     # 余额 0、并发满：重发不受影响
        Mock.mode = "ok"
        self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", "--retry-unknown")
        self.assertEqual(json.loads((task / "state.json").read_text(encoding="utf-8"))["task_id"], "task-1")

    def test_pending_lists_across_projects_and_flags_60min(self):
        self.settings(max_running_tasks=1)
        req, task = self._ready()
        self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args())
        # 另一部作品里放一个提交了 90 分钟的 unknown attempt
        other = self.manju / "作品" / "第二部"
        (other / "tasks" / "EP001-Clip00001-draft" / "attempt-001").mkdir(parents=True)
        st = {"provider": "minimax-h3", "status": "unknown", "submitted_at": time.time() - 5400, "task_id": None}
        (other / "tasks" / "EP001-Clip00001-draft" / "attempt-001" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        r = json.loads(self.run_cli("pending", "--all", "--project", self.manju).stdout)
        self.assertEqual(r["count"], 1)
        self.assertEqual(len(r["unknown"]), 1)
        self.assertTrue(r["unknown"][0]["over_60min"])
        self.assertEqual((r["cap"], r["slots_free"]), (1, 0))
        r1 = json.loads(self.run_cli("pending", "--project", self.d).stdout)      # 只看一部
        self.assertEqual(r1["count"], 1)
        self.assertEqual(r1["unknown"], [])

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe not installed")
    def test_poll_queries_downloads_and_settles(self):
        req, task = self._ready()
        out = self.d / "单集制作/EP001/试生成/视频_Clip00001.mp4"
        self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args(), "--output", out)
        Mock.mode = "ok"
        r = json.loads(self.run_cli("poll", "--all", "--project", self.manju).stdout)
        self.assertEqual((r["polled"], r["changed"]), (1, 1))
        self.assertEqual(r["results"][0]["result"]["status"], "succeeded")
        self.assertTrue(out.is_file())
        self.assertEqual(len(self.ledger()), 2)              # charge + settle

    def test_4xx_is_rejected_not_unknown(self):
        req, task = self._ready()
        Mock.mode = "400"
        self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args(), ok=False)
        self.assertEqual(json.loads((task / "state.json").read_text(encoding="utf-8"))["status"], "rejected")

    def test_changed_reference_blocks_submit(self):
        req, task = self._ready()
        req_data = json.loads(req.read_text(encoding="utf-8")); req_data["references"][0]["sha256"] = "0" * 64
        req.write_text(json.dumps(req_data), encoding="utf-8")
        r = self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args(), ok=False)
        self.assertIn("changed since prepare", r.stderr)

    # ---- query
    def test_query_pending(self):
        req, task = self._ready()
        self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args())
        Mock.mode = "running"
        r = self.run_cli("query", "--task-dir", task)
        self.assertEqual(json.loads(r.stdout)["status"], "pending")

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe not installed")
    def test_query_pending_then_download(self):
        req, task = self._ready()
        out = self.d / "out" / "clip.mp4"
        self.run_cli("submit", "--request", req, "--task-dir", task, "--execute", *self.budget_args(), "--output", out)
        Mock.mode = "running"
        r = self.run_cli("query", "--task-dir", task)
        self.assertEqual(json.loads(r.stdout)["status"], "pending")
        self.assertFalse(out.exists())
        Mock.mode = "ok"
        r = self.run_cli("query", "--task-dir", task)
        self.assertEqual(json.loads(r.stdout)["status"], "succeeded")
        self.assertTrue(out.is_file() and out.stat().st_size > 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
