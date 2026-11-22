#!/usr/bin/env python3
"""narrate.py 的离线测试：本机模拟 IndexTTS 2.5 接口，不联网、不消耗积分。运行：python -I test_narrate.py"""
import json, os, subprocess, sys, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "narrate.py"
TOOLS = HERE.parent
sys.path.insert(0, str(TOOLS))
import make_sample_project  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8")

KEY = "sk-ml-TEST-SECRET-123"

# 最小的合法 WAV（44 字节空文件头，ffprobe 没装时也能测 query 成功路径之外的所有逻辑）
WAV_MIN = (b"RIFF\x24\x08\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x40\x1f\x00\x00"
           b"\x40\x1f\x00\x00\x01\x00\x08\x00data\x00\x08\x00\x00" + b"\x80" * 2048)


def wav_with_seconds(seconds, rate=8000):
    import struct
    n = int(rate * seconds)
    hdr = (b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate, 1, 8)
           + b"data" + struct.pack("<I", n))
    return hdr + b"\x80" * n


HAS_FFPROBE = True
try:
    r = subprocess.run(["ffprobe", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=8000:d=1",
                        "-t", "0.2", "-f", "wav", "-"], capture_output=True, timeout=10)
except Exception:
    HAS_FFPROBE = False


class Mock(BaseHTTPRequestHandler):
    calls, mode, cost = [], "ok", 0.3
    jobs = {}                                               # task_id -> status
    wav_seconds = 3.2

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        Mock.calls.append(("GET", self.path, {}))
        if self.path.startswith("/audio/v1/pricing"):
            return self._send(200, {"enabled": True, "costs": {"text_to_speech": 1}, "unit_characters": 100,
                                    "balance": {"total_points": Mock.balance, "reserved_points": 0,
                                                "available_points": Mock.balance}})
        if self.path.startswith("/audio/v1/voices"):
            return self._send(200, {"items": [{"voice_id": "v1", "name": "承泽", "kind": "preset",
                                               "audio_url": "http://x/y.wav"}], "total": 1})
        if self.path.startswith("/audio/") and not self.path.startswith("/audio/v1/"):  # WAV 下载（output_url 指到本 mock）
            data = wav_with_seconds(Mock.wav_seconds) if HAS_FFPROBE else WAV_MIN
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        tid = self.path.rsplit("/", 1)[-1]
        job = Mock.jobs.get(tid)
        if job is None:
            return self._send(404, {"error": "no such job"})
        st = job["status"]
        body = {"task": {"task_id": tid, "status": st, "point_cost": Mock.cost if st == "succeeded" else None,
                         "point_status": "consumed" if st == "succeeded" else "reserved",
                         "billable_characters": job.get("chars", 24) if st == "succeeded" else None,
                         "error_message": job.get("error")}}
        if st == "succeeded":
            port = self.server.server_port
            body["task"]["output_url"] = f"http://127.0.0.1:{port}/audio/{tid}.wav"
        return self._send(200, body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Mock.calls.append(("POST", self.path, {"idem": self.headers.get("Idempotency-Key"), "body": body}))
        if self.path == "/audio/v1/speech/jobs":
            if Mock.mode == "500":
                return self._send(500, {"error": "upstream"})
            if Mock.mode == "400":
                return self._send(400, {"error": {"message": "bad request"}})
            if Mock.mode == "slow":
                time.sleep(3)
            tid = "job-" + self.headers.get("Idempotency-Key", "x")[:8]
            Mock.jobs[tid] = {"status": "queued", "chars": 24}
            return self._send(200, {"task_id": tid, "status": "queued"})
        return self._send(404, {"error": "nf"})

    def do_GET_audio(self, tid):
        pass


class MockAudio(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        data = wav_with_seconds(Mock.wav_seconds) if HAS_FFPROBE else WAV_MIN
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class NarrateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.audio_srv = ThreadingHTTPServer(("127.0.0.1", 0), MockAudio)
        threading.Thread(target=cls.audio_srv.serve_forever, daemon=True).start()
        # 测试里把 H3_BASE_URL 指到同一个 mock：narrate.py 的音频下载走绝对 URL（mock 返回本服务器的端口）
        cls.tmp = Path(tempfile.mkdtemp())

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.audio_srv.shutdown()

    def setUp(self):
        Mock.calls.clear()
        Mock.mode, Mock.cost = "ok", 0.3
        Mock.balance = 1000
        Mock.jobs.clear()
        Mock.wav_seconds = 3.2
        self.tmp = tempfile.TemporaryDirectory()
        self.manju = Path(self.tmp.name) / "漫剧"
        self.root = make_sample_project.build(self.manju / "作品" / "样例作品")
        (self.root / "approvals").mkdir(exist_ok=True)
        (self.root / "approvals" / "budget.json").write_text(json.dumps(
            [{"batch": "EP001-旁白", "kind": "speech", "unit": "points", "limit": 50, "used": 0}]), encoding="utf-8")
        (self.root / 'approvals' / 'tts-catalog.json').write_text(json.dumps(
            {'voices': [{'name': '承泽', 'voice_id': 'mock-v1'}], 'unit_price': .01}), encoding='utf-8')
        # 给样例加旁白（剪辑不能加 text，测试直接改文件模拟编剧写好的状态）
        clips = json.loads((self.root / "clips.json").read_text(encoding="utf-8"))
        clips[0]["narration"] = [{"id": "Clip00001-1", "start": 0, "reserved": 4,
                                  "text": "那一夜，山门外的雪下得很大。"}]
        (self.root / "clips.json").write_text(json.dumps(clips, ensure_ascii=False, indent=2), encoding="utf-8")
        self.task = self.root / "tasks" / "EP001-旁白-Clip00001-1" / "attempt-001"

    def cli(self, *args, ok=True, code=None):
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "MINIMAX_H3_API_KEY": KEY,
               "H3_BASE_URL": f"http://127.0.0.1:{self.srv.server_port}"}
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        out = r.stdout + r.stderr
        self.assertNotIn(KEY, out, "密钥泄露到输出")
        if code is not None:
            self.assertEqual(r.returncode, code, out)
        elif ok:
            self.assertEqual(r.returncode, 0, out)
        else:
            self.assertNotEqual(r.returncode, 0, out)
        return r

    def budget_args(self):
        return ["--budget", self.root / "approvals" / "budget.json", "--batch", "EP001-旁白"]

    def used(self):
        data = json.loads((self.root / "approvals" / "budget.json").read_text(encoding="utf-8"))
        return data[0]["used"]

    def ledger(self):
        f = self.manju / "全局账本.jsonl"
        return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []

    def clips(self):
        return json.loads((self.root / "clips.json").read_text(encoding="utf-8"))

    def settings(self, **h3):
        f = self.manju / "全局设置.json"
        s = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"h3": {}, "nexorai": {}}
        s["h3"].update(h3)
        f.write_text(json.dumps(s), encoding="utf-8")

    # ---- prepare
    def test_prepare_estimates_and_writes_requests(self):
        r = json.loads(self.cli("prepare", "--project", self.root, "--episode", "EP001").stdout)
        self.assertEqual(r["todo"], 1)
        self.assertEqual(r["total_estimate_points"], 0.12)          # 14 个汉字字母数字 × 1/100
        reqs = json.loads((self.root / "单集制作/EP001/旁白/requests.json").read_text(encoding="utf-8"))["requests"]
        self.assertEqual(reqs[0]["params"]["voice"]["voice_id"], "mock-v1")
        self.assertEqual(reqs[0]["provider"], "compshare-indextts")

    def test_prepare_skips_done_and_respects_episode(self):
        clips = self.clips()
        clips[0]["narration"][0]["audio"] = "单集制作/EP001/旁白/x.wav"
        (self.root / "单集制作/EP001/旁白").mkdir(parents=True, exist_ok=True)
        (self.root / "单集制作/EP001/旁白/x.wav").write_bytes(WAV_MIN)
        (self.root / "clips.json").write_text(json.dumps(clips, ensure_ascii=False), encoding="utf-8")
        r = json.loads(self.cli("prepare", "--project", self.root).stdout)
        self.assertEqual(r["todo"], 0)
        clips[0]["episode"] = "EP002"                               # 不在这一集
        (self.root / "clips.json").write_text(json.dumps(clips, ensure_ascii=False), encoding="utf-8")
        r = json.loads(self.cli("prepare", "--project", self.root, "--episode", "EP001").stdout)
        self.assertEqual(r["todo"], 0)

    def test_prepare_needs_narration_config(self):
        pj = json.loads((self.root / "project.json").read_text(encoding="utf-8"))
        del pj["narration"]
        (self.root / "project.json").write_text(json.dumps(pj, ensure_ascii=False), encoding="utf-8")
        r = self.cli("prepare", "--project", self.root, ok=False)
        self.assertIn("narration", r.stderr)

    # ---- submit
    def _requests(self):
        self.cli("prepare", "--project", self.root, "--episode", "EP001")
        return self.root / "单集制作/EP001/旁白/requests.json"

    def test_preview_makes_no_call(self):
        req = self._requests()
        r = self.cli("submit", "--request", req, "--entry", "Clip00001-1", "--task-dir", self.task)
        self.assertTrue(json.loads(r.stdout)["preview"])
        self.assertFalse(self.task.exists())
        # 预估需要单价（GET pricing 免费只读），但不允许任何创建任务的 POST
        self.assertEqual([c for c in Mock.calls if c[0] == "POST"], [])

    def test_submit_query_succeeds_and_writes_back(self):
        req = self._requests()
        out = self.root / "单集制作" / "EP001" / "旁白" / "Clip00001-1.wav"
        r = self.cli("submit", "--request", req, "--entry", "Clip00001-1", "--task-dir", self.task,
                     "--execute", "--output", out, *self.budget_args())
        st = json.loads((self.task / "state.json").read_text(encoding="utf-8"))
        tid = st["task_id"]
        self.assertEqual(self.used(), 0.12)
        self.assertEqual([e["event"] for e in self.ledger()], ["charge"])
        post = [c for c in Mock.calls if c[0] == "POST"][0]
        self.assertTrue(post[2]["idem"])
        self.assertEqual(json.loads(post[2]["body"])["voice"]["voice_id"], "mock-v1")
        Mock.jobs[tid]["status"] = "succeeded"
        r = json.loads(self.cli("query", "--task-dir", self.task).stdout)
        self.assertEqual(r["status"], "succeeded")
        self.assertEqual(r["narration_duration"], Mock.wav_seconds)
        self.assertTrue(out.is_file())
        n = self.clips()[0]["narration"][0]
        self.assertEqual(n["audio"], "单集制作/EP001/旁白/Clip00001-1.wav")
        self.assertEqual(n["narration_duration"], Mock.wav_seconds)
        settle = [e for e in self.ledger() if e["event"] == "settle"][0]
        self.assertEqual((settle["estimated_points"], settle["actual_points"], settle["adjusted"]), (0.12, 0.3, -0.18))
        self.assertEqual(self.used(), 0.3)                          # 实际比预估多：补扣

    def test_reserved_overrun_warns(self):
        Mock.wav_seconds = 5.5                                      # 预留 4s
        req = self._requests()
        out = self.root / "单集制作" / "EP001" / "旁白" / "Clip00001-1.wav"
        self.cli("submit", "--request", req, "--entry", "Clip00001-1", "--task-dir", self.task,
                 "--execute", "--output", out, *self.budget_args())
        tid = json.loads((self.task / "state.json").read_text(encoding="utf-8"))["task_id"]
        Mock.jobs[tid]["status"] = "succeeded"
        r = self.cli("query", "--task-dir", self.task)
        self.assertIn("超过预留", r.stderr)

    def test_rejected_refunds(self):
        Mock.mode = "400"
        req = self._requests()
        self.cli("submit", "--request", req, "--entry", "Clip00001-1", "--task-dir", self.task,
                 "--execute", *self.budget_args(), ok=False)
        st = json.loads((self.task / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(st["status"], "rejected")
        self.assertTrue(st["charge"]["refunded"])
        self.assertEqual(self.used(), 0)
        self.assertEqual([e["event"] for e in self.ledger()], ["charge", "refund"])

    def test_unknown_then_retry_reuses_key(self):
        Mock.mode = "500"
        req = self._requests()
        self.cli("submit", "--request", req, "--entry", "Clip00001-1", "--task-dir", self.task,
                 "--execute", *self.budget_args(), ok=False)
        self.assertEqual(json.loads((self.task / "state.json").read_text(encoding="utf-8"))["status"], "unknown")
        Mock.mode = "ok"
        self.cli("submit", "--request", req, "--entry", "Clip00001-1", "--task-dir", self.task,
                 "--execute", "--retry-unknown")
        keys = [c[2]["idem"] for c in Mock.calls if c[0] == "POST"]
        self.assertEqual(len(set(keys)), 1)
        self.assertEqual(json.loads((self.task / "state.json").read_text(encoding="utf-8"))["status"], "submitted")

    def test_execute_requires_budget(self):
        req = self._requests()
        r = self.cli("submit", "--request", req, "--entry", "Clip00001-1", "--task-dir", self.task, "--execute", ok=False)
        self.assertIn("no unbudgeted submits", r.stderr)
        self.assertEqual([c for c in Mock.calls if c[0] == "POST"], [])

    def test_insufficient_balance_rejects(self):
        Mock.balance = 0.05
        req = self._requests()
        r = self.cli("submit", "--request", req, "--entry", "Clip00001-1", "--task-dir", self.task,
                     "--execute", *self.budget_args(), ok=False)
        self.assertIn("余额不够", r.stderr)
        self.assertEqual(self.used(), 0)
        self.assertFalse((self.task / "submission.lock").exists())

    def test_concurrency_cap_queues(self):
        self.settings(max_running_tasks=1)
        req = self._requests()
        # 先放一个占名额的 H3 任务（别的作品的 attempt）
        other = self.manju / "作品" / "第二部"
        (other / "tasks" / "T" / "attempt-001").mkdir(parents=True)
        (other / "tasks" / "T" / "attempt-001" / "state.json").write_text(json.dumps(
            {"provider": "minimax-h3", "status": "submitted", "submitted_at": time.time()}), encoding="utf-8")
        r = self.cli("submit", "--request", req, "--entry", "Clip00001-1", "--task-dir", self.task,
                     "--execute", *self.budget_args(), code=4)
        self.assertIn("排队中", r.stderr)
        self.assertEqual(self.used(), 0)

    def test_speech_counts_in_global_concurrency(self):
        self.settings(max_running_tasks=2)
        req = self._requests()
        other = self.manju / "作品" / "第二部"
        (other / "tasks" / "S" / "attempt-001").mkdir(parents=True)
        (other / "tasks" / "S" / "attempt-001" / "state.json").write_text(json.dumps(
            {"provider": "compshare-indextts", "status": "generating", "submitted_at": time.time()}), encoding="utf-8")
        self.cli("submit", "--request", req, "--entry", "Clip00001-1", "--task-dir", self.task,
                 "--execute", *self.budget_args())                  # 2 个占用，还能放 1 个
        self.assertEqual(self.used(), 0.12)

    def test_voices_lists_presets(self):
        r = json.loads(self.cli("voices").stdout)
        self.assertEqual(r["voices"][0]["name"], "承泽")
        self.assertNotIn("audio_url", r["voices"][0])               # 试听链接默认隐藏

    def test_budget_must_be_speech_batch(self):
        req = self._requests()
        (self.root / "approvals" / "budget.json").write_text(json.dumps(
            [{"batch": "EP001-旁白", "kind": "video", "unit": "points", "limit": 50, "used": 0}]), encoding="utf-8")
        r = self.cli("submit", "--request", req, "--entry", "Clip00001-1", "--task-dir", self.task,
                     "--execute", *self.budget_args(), ok=False)
        self.assertIn("speech", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
