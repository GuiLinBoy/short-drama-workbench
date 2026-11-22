#!/usr/bin/env python3
"""nexorai_image.py 的离线测试：本机模拟接口，不联网、不消耗 NexorAI 额度。运行：python -I test_nexorai_image.py"""
import base64, json, os, re, shutil, struct, subprocess, sys, tempfile, threading, time, unittest, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "nexorai_image.py"
TOOLS = HERE.parents[2]
sys.path.insert(0, str(TOOLS / "tools"))
import make_sample_project  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8")

KEY = "nx-test-key-should-never-appear-0123456789"
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def png(w, h, rgb=(90, 120, 200)):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    row = b"\x00" + bytes(rgb) * w
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * h)) + chunk(b"IEND", b""))


class Mock(BaseHTTPRequestHandler):
    calls, mode, size, n = [], "ok", (720, 1280), 1

    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, raw=None, ctype="application/json", headers=()):
        data = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except ConnectionError:      # 超时测试里客户端已经先断开
            pass

    def do_GET(self):
        Mock.calls.append(("GET", self.path, dict(self.headers), b""))
        if self.path == "/v1/models":
            return self._send(200, {"data": [{"id": "grok-imagine-image-quality"}, {"id": "gpt-image-2"}, {"id": "claude-x"}]})
        if self.path.startswith("/img/"):
            return self._send(200, raw=png(*Mock.size), ctype="image/png")
        self._send(404, {"error": "nf"})

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Mock.calls.append(("POST", self.path, dict(self.headers), body))
        m = Mock.mode
        if m == "slow":
            time.sleep(3)
        if m == "blocked":
            return self._send(400, {"error": {"message": "Your request was rejected by our content policy"}})
        if m == "quota":
            return self._send(402, {"error": {"message": "insufficient quota"}})
        if m == "rate":
            return self._send(429, {"error": {"message": "slow down"}}, headers=[("Retry-After", "30")])
        if m == "e500":
            return self._send(500, {"error": "upstream"})
        if m == "noimage":
            return self._send(200, {"data": []})
        port = self.server.server_port
        items = []
        for i in range(Mock.n):
            if m == "url":
                items.append({"url": f"http://127.0.0.1:{port}/img/{i}.png"})
            elif m == "urlfail":
                items.append({"url": f"http://127.0.0.1:{port}/missing/{i}.png"})
            elif m == "badimage":
                items.append({"b64_json": base64.b64encode(b"\x89PNG\r\n\x1a\nnot really a png").decode()})
            else:
                items.append({"b64_json": base64.b64encode(png(*Mock.size)).decode()})
        self._send(200, {"created": 1, "data": items})


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe 不在 PATH 里")
class NexorAIImageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Mock.calls.clear()
        Mock.mode, Mock.size, Mock.n = "ok", (720, 1280), 1
        self.tmp = tempfile.TemporaryDirectory()
        self.manju = Path(self.tmp.name) / "漫剧"
        self.root = make_sample_project.build(self.manju / "作品" / "样例作品")
        self.ref_png = self.root / "角色/C00001-A-林舟.png"

    def tearDown(self):
        self.tmp.cleanup()

    # ---- helpers
    def cli(self, *args, ok=True, code=None):
        env = {**os.environ, "PYTHONUTF8": "1", "NEXORAI_API_KEY": KEY,
               "NEXORAI_BASE_URL": f"http://127.0.0.1:{self.srv.server_port}/v1"}
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)], capture_output=True, text=True,
                           encoding="utf-8", env=env)
        out = r.stdout + r.stderr
        self.assertNotIn(KEY, out, "密钥泄露到输出")
        self.assertNotRegex(out, r"[A-Za-z0-9+/]{400,}", "base64 出现在输出里")
        if code is not None:
            self.assertEqual(r.returncode, code, out)
        elif ok:
            self.assertEqual(r.returncode, 0, out)
        else:
            self.assertNotEqual(r.returncode, 0, out)
        return r

    def prompt(self, text, name="p.txt"):
        f = self.root / "提示词" / name
        f.parent.mkdir(exist_ok=True)
        f.write_text(text, encoding="utf-8")
        return f

    def prepare(self, asset="C00002-A", kind="image", text="古风少女，白衣，全身站姿，浅色纯色背景", refs=(),
                extra=(), ok=True, name="req.json"):
        args = ["prepare", "--project", self.root, "--asset", asset, "--kind", kind, "--prompt", self.prompt(text),
                "--output", self.root / "tasks" / "req" / name]
        for r in refs:
            args += ["--ref", r]
        return self.cli(*args, *extra, ok=ok)

    def req(self, name="req.json"):
        return self.root / "tasks" / "req" / name

    def attempt(self, n=1, task="C00002-A-image"):
        return self.root / "tasks" / task / f"attempt-{n:03d}"

    def submit(self, n=1, extra=(), ok=True, code=None, name="req.json", batch="EP001-参考图"):
        return self.cli("submit", "--request", self.req(name), "--task-dir", self.attempt(n), "--execute",
                        "--budget", self.root / "approvals" / "budget.json", "--batch", batch, *extra, ok=ok, code=code)

    def used(self, batch="EP001-参考图"):
        data = json.loads((self.root / "approvals/budget.json").read_text(encoding="utf-8"))
        return next(b for b in data if b["batch"] == batch)["used"]

    def state(self, n=1):
        return json.loads((self.attempt(n) / "state.json").read_text(encoding="utf-8"))

    def ledger(self):
        f = self.manju / "全局账本.jsonl"
        return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []

    def size_of(self, f):
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(f)],
                           capture_output=True, text=True)
        return tuple(int(x) for x in r.stdout.strip().split(","))

    def posts(self):
        return [c for c in Mock.calls if c[0] == "POST"]

    def set_genre(self, genre):
        pj = json.loads((self.root / "project.json").read_text(encoding="utf-8"))
        pj["genre"] = genre
        (self.root / "project.json").write_text(json.dumps(pj, ensure_ascii=False), encoding="utf-8")

    # ---- prepare：选模型
    def test_model_follows_genre_and_grok_gets_aspect_ratio(self):
        r = json.loads(self.prepare().stdout)
        self.assertEqual((r["model"], r["endpoint"], r["ratio"]), ("grok-imagine-image-quality", "/images/generations", "9:16"))
        params = json.loads(self.req().read_text(encoding="utf-8"))["params"]
        self.assertEqual(params["aspect_ratio"], "9:16")
        self.assertNotIn("size", params)
        for genre, model in (("live-action", "grok-imagine-image-2.0"), ("urban", "grok-imagine-image-2.0")):
            self.set_genre(genre)
            self.assertEqual(json.loads(self.prepare(name=f"{genre}.json").stdout)["model"], model)

    def test_rank_and_override_need_reason(self):
        self.assertEqual(json.loads(self.prepare(extra=("--rank", "2")).stdout)["model"], "gpt-image-2.5-sunburst")
        self.prepare(extra=("--model", "gpt-image-2"), ok=False, name="o1.json")
        r = json.loads(self.prepare(extra=("--model", "gpt-image-2", "--reason", "测试"), name="o2.json").stdout)
        self.assertIn("没用题材配置里的模型", " ".join(r["warnings"]))
        params = json.loads(self.req("o2.json").read_text(encoding="utf-8"))["params"]
        self.assertEqual((params["size"], params["quality"]), ("864x1536", "high"))
        self.assertNotIn("aspect_ratio", params)

    def test_gpt_refused_for_landscape(self):
        r = self.prepare("C00001-A", "sheet", "以图1为参考出三视图", refs=(f"人物={self.ref_png}",),
                         extra=("--model", "gpt-image-2.5", "--reason", "x"), ok=False)
        self.assertIn("横图", r.stderr)

    def test_sheet_uses_sheet_ratio_and_needs_person_ref(self):
        self.prepare("C00001-A", "sheet", "三视图", ok=False)
        r = json.loads(self.prepare("C00001-A", "sheet", "以图1为参考出三视图", refs=(f"人物={self.ref_png}",), name="s.json").stdout)
        self.assertEqual((r["ratio"], r["endpoint"]), ("16:9", "/images/edits"))

    def test_figure_numbers_must_match_refs(self):
        r = self.prepare(text="参考图2的姿势", refs=(f"人物={self.ref_png}",), ok=False)
        self.assertIn("有几张图就编几个号", r.stderr)
        self.prepare(text="参考图1", ok=False, name="b.json")
        r = json.loads(self.prepare(text="人物同图1", refs=(f"人物={self.ref_png}", f"姿势={self.ref_png}"), name="c.json").stdout)
        self.assertIn("图[2]", " ".join(r["warnings"]))
        self.prepare(refs=(f"表情={self.ref_png}",), ok=False, name="d.json")

    def test_kind_must_match_asset_type_and_target_path(self):
        self.prepare("S00001", "image", ok=False)
        self.prepare("C00002-A", "scene", ok=False, name="x.json")
        self.prepare("C00002-A", "sheet", "图1", refs=(f"人物={self.ref_png}",), ok=False, name="y.json")  # 没有 sheet 路径

    def test_real_person_warning_for_live_action(self):
        self.set_genre("live-action")
        r = json.loads(self.prepare(text="长得像某明星的年轻女子，全身站姿").stdout)
        self.assertIn("真实存在的人", " ".join(r["warnings"]))

    # ---- submit：预览和闸门
    def test_preview_makes_no_call(self):
        self.prepare()
        r = self.cli("submit", "--request", self.req(), "--task-dir", self.attempt())
        self.assertTrue(json.loads(r.stdout)["preview"])
        self.assertEqual(Mock.calls, [])

    def test_budget_and_layout_gates(self):
        self.prepare()
        r = self.cli("submit", "--request", self.req(), "--task-dir", self.attempt(), "--execute", ok=False)
        self.assertIn("没有批次不能提交", r.stderr)
        rogue = self.root / "rogue.json"
        rogue.write_text('[{"batch":"B","kind":"image","unit":"images","limit":99,"used":0}]', encoding="utf-8")
        r = self.cli("submit", "--request", self.req(), "--task-dir", self.attempt(), "--execute", "--budget", rogue,
                     "--batch", "B", ok=False)
        self.assertIn("approvals/budget.json", r.stderr)
        self.cli("submit", "--request", self.req(), "--task-dir", self.root / "attempt-001", "--execute", "--budget",
                 self.root / "approvals/budget.json", "--batch", "EP001-参考图", ok=False)
        r = self.submit(ok=False, batch="EP001-试生成")
        self.assertIn("不是生图批次", r.stderr)
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.used(), 0)

    def test_changed_reference_blocks_submit(self):
        ref = self.root / "参考" / "人物.png"
        ref.parent.mkdir()
        ref.write_bytes(png(64, 64))
        self.prepare("C00001-A", "sheet", "图1 三视图", refs=(f"人物={ref}",))
        ref.write_bytes(png(64, 64, (1, 2, 3)))
        r = self.cli("submit", "--request", self.req(), "--task-dir", self.attempt(task="C00001-A-sheet"), "--execute",
                     "--budget", self.root / "approvals/budget.json", "--batch", "EP001-参考图", ok=False)
        self.assertIn("变了", r.stderr)

    # ---- 成功和交付
    def test_success_delivers_exact_ratio_and_charges(self):
        self.prepare()
        out = json.loads(self.submit().stdout)
        self.assertEqual(out["status"], "succeeded")
        target = self.root / "角色/C00002-A-苏晚.png"
        self.assertEqual(self.size_of(target), (720, 1280))
        self.assertEqual(self.used(), 1)
        self.assertEqual([e["event"] for e in self.ledger()], ["charge"])
        hdr = self.posts()[0][2]
        self.assertEqual(hdr["Authorization"], "Bearer " + KEY)
        self.assertTrue(hdr["User-Agent"].startswith("novel-to-video"))
        body = json.loads(self.posts()[0][3])
        self.assertEqual((body["aspect_ratio"], body["n"]), ("9:16", 1))
        for f in ("state.json", "response.json", "request.json"):
            text = (self.attempt() / f).read_bytes()
            self.assertNotIn(KEY.encode(), text)
            self.assertNotIn(b"\r\n", text)
        self.assertIn("base64 已省略", (self.attempt() / "response.json").read_text(encoding="utf-8"))
        self.assertIn("set-asset C00002-A --set status=generated", " ".join(out["next"]))
        r = self.submit(ok=False)                          # 同一个 attempt 不能再提交
        self.assertIn("已经提交过", r.stderr)
        self.assertEqual(len(self.posts()), 1)

    def test_url_response_and_two_candidates(self):
        Mock.mode, Mock.n = "url", 2
        self.prepare(extra=("--n", "2"))
        out = json.loads(self.submit().stdout)
        self.assertEqual(out["status"], "succeeded")
        self.assertTrue((self.root / "角色/C00002-A-苏晚-候选2.png").is_file())
        self.assertEqual(self.used(), 2)

    def test_near_ratio_is_center_cropped(self):
        Mock.size = (1024, 1536)
        self.prepare(extra=("--model", "gpt-image-2.5", "--reason", "测细节"))
        out = json.loads(self.submit().stdout)
        d = out["deliveries"][0]
        self.assertEqual((d["action"], d["final_size"]), ("crop", [864, 1536]))
        self.assertIn("居中裁切", d["note"])
        self.assertTrue((self.attempt() / "original-1.png").is_file())     # 原图保留

    def test_far_ratio_is_not_delivered(self):
        Mock.size = (1280, 720)
        self.prepare()
        out = json.loads(self.submit().stdout)
        self.assertEqual(out["status"], "needs_recompose")
        self.assertFalse((self.root / "角色/C00002-A-苏晚.png").exists())
        self.assertEqual(self.used(), 1)                   # 图出了，额度照算

    def test_edit_uploads_jpg_references_in_order(self):
        Mock.size = (1280, 720)
        self.prepare("C00001-A", "sheet", "图1是人物，图2是姿势，出三视图", refs=(f"人物={self.ref_png}", f"姿势={self.ref_png}"))
        self.cli("submit", "--request", self.req(), "--task-dir", self.attempt(task="C00001-A-sheet"), "--execute",
                 "--budget", self.root / "approvals/budget.json", "--batch", "EP001-参考图")
        path, headers, body = self.posts()[0][1:]
        self.assertEqual(path, "/v1/images/edits")
        self.assertTrue(headers["Content-Type"].startswith("multipart/form-data"))
        self.assertEqual(body.count(b'name="image[]"'), 2)
        self.assertEqual(body.count(b"Content-Type: image/jpeg"), 2)
        self.assertIn(b'name="aspect_ratio"\r\n\r\n16:9', body)
        self.assertIn(b"\xff\xd8", body)                    # 真的转成了 JPG
        self.assertEqual(self.size_of(self.root / "角色/C00001-A-林舟.sheet.png"), (1280, 720))

    def test_bad_image_keeps_charge(self):
        Mock.mode = "badimage"
        self.prepare()
        self.assertEqual(json.loads(self.submit().stdout)["status"], "bad_image")
        self.assertEqual(self.used(), 1)

    def test_url_download_failure_then_fetch(self):
        Mock.mode = "urlfail"
        self.prepare()
        self.assertEqual(json.loads(self.submit().stdout)["status"], "download_failed")
        st = self.state()
        st["pending_urls"] = [u.replace("/missing/", "/img/") for u in st["pending_urls"]]   # 模拟链接恢复可用
        (self.attempt() / "state.json").write_text(json.dumps(st), encoding="utf-8")
        out = json.loads(self.cli("fetch", "--task-dir", self.attempt()).stdout)
        self.assertEqual(out["status"], "succeeded")
        self.assertEqual(len(self.posts()), 1)             # 没有重新生成

    # ---- 失败和退款
    def test_definite_failures_refund(self):
        for mode, status in (("blocked", "blocked"), ("rate", "rate_limited")):
            Mock.mode = mode
            self.prepare(name=f"{mode}.json")
            r = self.cli("submit", "--request", self.req(f"{mode}.json"), "--task-dir", self.attempt(task=mode),
                         "--execute", "--budget", self.root / "approvals/budget.json", "--batch", "EP001-参考图", code=3)
            st = json.loads((self.attempt(task=mode) / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(st["status"], status, r.stderr)
            self.assertTrue(st["charge"]["refunded"])
            self.assertEqual(self.used(), 0)
        self.assertEqual(json.loads((self.attempt(task="rate") / "state.json").read_text(encoding="utf-8"))["retry_after"], "30")

    def test_ambiguous_server_error_and_empty_response_keep_reservation(self):
        for mode in ("e500", "noimage"):
            Mock.mode = mode
            self.prepare(name=f"{mode}.json")
            r = self.cli("submit", "--request", self.req(f"{mode}.json"), "--task-dir", self.attempt(task=mode),
                         "--execute", "--budget", self.root / "approvals/budget.json", "--batch", "EP001-参考图", ok=False)
            st = json.loads((self.attempt(task=mode) / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(st["status"], "unknown")
            self.assertFalse(st["charge"]["refunded"])
        self.assertEqual(self.used(), 2)

    def test_quota_exhausted_pauses_today_until_resume(self):
        Mock.mode = "quota"
        self.prepare()
        r = self.submit(code=3)
        self.assertIn("暂停", r.stderr)
        self.assertEqual((self.state()["status"], self.used()), ("quota_exhausted", 0))
        Mock.mode = "ok"
        self.prepare(name="r2.json")
        r = self.submit(2, ok=False, name="r2.json")
        self.assertIn("已暂停", r.stderr)
        self.assertEqual(len(self.posts()), 1)
        self.cli("resume", "--project", self.root, "--note", "已充值")
        self.assertEqual(json.loads(self.submit(2, name="r2.json").stdout)["status"], "succeeded")

    def test_timeout_is_unknown_and_needs_acknowledgement(self):
        Mock.mode = "slow"
        self.prepare()
        self.submit(code=2, extra=("--timeout", "1"))
        self.assertEqual((self.state()["status"], self.used()), ("unknown", 1))      # 结果不明：额度保持扣除
        Mock.mode = "ok"
        r = self.submit(2, ok=False)
        self.assertIn("--after-unknown attempt-001", r.stderr)
        self.assertEqual(self.used(), 1)
        self.submit(2, extra=("--after-unknown", "attempt-001"))
        self.assertEqual(self.used(), 2)

    def test_daily_and_batch_caps(self):
        (self.manju / "全局设置.json").write_text(json.dumps(
            {"nexorai": {"images_cap_per_day": 2, "images_per_batch": 12, "max_running": 2}}), encoding="utf-8")
        Mock.n = 2
        self.prepare(extra=("--n", "2"))
        self.submit()
        Mock.n = 1
        self.prepare(name="c2.json")
        r = self.submit(2, ok=False, name="c2.json")
        self.assertIn("每天上限 2", r.stderr)
        self.assertFalse((self.attempt(2) / "submission.lock").exists())             # 被拒的 attempt 还能用
        (self.manju / "全局设置.json").write_text(json.dumps(
            {"nexorai": {"images_cap_per_day": 50, "images_per_batch": 5, "max_running": 2}}), encoding="utf-8")
        r = self.submit(2, ok=False, name="c2.json")
        self.assertIn("单批上限 5", r.stderr)
        self.assertEqual(self.used(), 2)

    def test_settings_created_with_conservative_defaults(self):
        self.prepare()
        self.submit()
        s = json.loads((self.manju / "全局设置.json").read_text(encoding="utf-8"))
        self.assertEqual((s["nexorai"]["images_cap_per_day"], s["nexorai"]["images_per_batch"]), (50, 12))
        self.assertNotIn("points_cap", json.dumps(s))

    def test_image_concurrency_cap_queues_without_charging(self):
        (self.manju / "全局设置.json").write_text(json.dumps(
            {"nexorai": {"images_cap_per_day": 50, "images_per_batch": 12, "max_running": 1}}), encoding="utf-8")
        self.prepare()
        # 别的 attempt 正在出图（同步 API 的运行窗口就是 state 停在 submitting）
        busy = self.attempt(task="C00003-A-image")
        busy.mkdir(parents=True)
        (busy / "state.json").write_text(json.dumps(
            {"provider": "nexorai", "task_dir": str(busy), "status": "submitting",
             "submitted_at": "2026-11-05T10:00:00"}), encoding="utf-8")
        r = self.submit(code=4)                              # 并发满：排队，不报错、不扣额度
        self.assertIn("排队中", r.stderr)
        self.assertEqual(self.used(), 0)
        self.assertFalse((self.attempt() / "submission.lock").exists())
        self.assertEqual(self.posts(), [])
        (busy / "state.json").write_text(json.dumps(
            {"provider": "nexorai", "task_dir": str(busy), "status": "succeeded"}), encoding="utf-8")  # 出图结束
        self.assertEqual(json.loads(self.submit().stdout)["status"], "succeeded")

    def test_concurrent_submits_do_not_exceed_cap(self):
        (self.manju / "全局设置.json").write_text(json.dumps(
            {"nexorai": {"images_cap_per_day": 50, "images_per_batch": 12, "max_running": 1}}), encoding="utf-8")
        self.prepare()
        Mock.mode = "slow"                                   # 让第一个请求挂在网上，state 停在 submitting
        env = {**os.environ, "PYTHONUTF8": "1", "NEXORAI_API_KEY": KEY,
               "NEXORAI_BASE_URL": f"http://127.0.0.1:{self.srv.server_port}/v1"}
        args = ["submit", "--request", str(self.req()), "--task-dir", str(self.attempt()), "--execute",
                "--budget", str(self.root / "approvals/budget.json"), "--batch", "EP001-参考图"]
        procs = []
        for i in range(3):
            different = list(args)
            different[different.index("--task-dir") + 1] = str(self.attempt(i + 1))
            procs.append(subprocess.Popen([sys.executable, "-I", str(SCRIPT), *different], stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, env=env))
        codes = [p.wait() for p in procs]
        Mock.mode = "ok"
        self.assertEqual(codes.count(0), 1)                  # 只有一个真正提交
        self.assertEqual(codes.count(4), 2)                  # 其余排队
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(self.used(), 1)

    def test_models_lists_image_models(self):
        out = json.loads(self.cli("models").stdout)
        self.assertEqual(out["image_models"], ["gpt-image-2", "grok-imagine-image-quality"])


class ProfileMatchesGenres(unittest.TestCase):
    ROWS = {"角色视频参考图": "reference", "衍生形态、三视图": "edit", "场景图": "scene"}

    def test_priority_tables_match_genre_configs(self):
        profile = json.loads((TOOLS / "profiles/nexorai.json").read_text(encoding="utf-8"))
        for genre, table in profile["priority"].items():
            text = (TOOLS / "genres" / f"{genre}.md").read_text(encoding="utf-8")
            section = text.split("## 2.", 1)[1].split("\n## ", 1)[0]
            found = {}
            for line in section.splitlines():
                for label, key in self.ROWS.items():
                    if line.startswith("| " + label):
                        found[key] = re.findall(r"`([^`]+)`", line)
            self.assertEqual(found, table, f"genres/{genre}.md 第 2 节和 profiles/nexorai.json 不一致")
        for alias, base in profile["genre_alias"].items():
            self.assertIn(base, profile["priority"])
            self.assertIn(f"{base}.md", (TOOLS / "genres" / f"{alias}.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
