#!/usr/bin/env python3
"""pipeline_state.py / validate_project.py / make_sample_project.py 的离线测试。运行：python -I test_tools.py"""
import json, subprocess, sys, tempfile, unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_sample_project  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8")


def run(script, *args, ok=True):
    r = subprocess.run([sys.executable, "-I", str(HERE / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8")
    if ok is True and r.returncode != 0:
        raise AssertionError(f"{script} {args} 失败：{r.stderr}")
    if ok is False and r.returncode == 0:
        raise AssertionError(f"{script} {args} 本应被拒绝：{r.stdout}")
    return r


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_sample_project.build(Path(self.tmp.name) / "样例作品")

    def tearDown(self):
        self.tmp.cleanup()

    def ps(self, role, *args, ok=True):
        pre = ["--project", self.root] + (["--role", role] if role else [])
        return run("pipeline_state.py", *pre, *args, ok=ok)

    def load(self, name):
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    def save(self, name, data):
        (self.root / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def put(self, rel, data=b"fake media"):
        f = self.root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        return f

    def clip(self, cid):
        return next(c for c in self.load("clips.json") if c["id"] == cid)

    def asset(self, aid):
        return next(a for a in self.load("assets.json") if a["id"] == aid)

    def validate(self, stage="plan", episode=None):
        extra = ["--episode", episode] if episode else []
        r = run("validate_project.py", "--project-root", self.root, "--stage", stage, *extra, ok=None)
        return json.loads(r.stdout)


class PipelineStateTest(Base):
    NEW_ASSET = {"id": "C00003-A", "type": "character", "name": "老掌门", "detail": "角色/C00003-A-老掌门.txt",
                 "prompt": "角色/C00003-A-老掌门.prompt.txt", "image": "角色/C00003-A-老掌门.png"}

    def test_writer_creates_asset_with_planned_status(self):
        self.ps("编剧", "add-asset", "--json", json.dumps(self.NEW_ASSET, ensure_ascii=False))
        self.assertEqual(self.asset("C00003-A")["status"], "planned")

    def test_only_writer_creates_and_only_planned(self):
        self.ps("美术", "add-asset", "--json", json.dumps(self.NEW_ASSET, ensure_ascii=False), ok=False)
        bad = {**self.NEW_ASSET, "status": "generated"}
        r = self.ps("编剧", "add-asset", "--json", json.dumps(bad, ensure_ascii=False), ok=False)
        self.assertIn("planned", r.stderr)
        r = self.ps("编剧", "add-asset", "--json", json.dumps({**self.NEW_ASSET, "id": "X1"}), ok=False)
        self.assertIn("编号格式", r.stderr)

    def test_initial_path_by_creator_then_owner_only(self):
        self.ps("编剧", "set-asset", "C00002-A", "--set", "image=角色/别的.png", ok=False)
        self.ps("美术", "set-asset", "C00002-A", "--set", "image=角色/C00002-A-苏晚-v2.png")
        self.assertEqual(self.asset("C00002-A")["image"], "角色/C00002-A-苏晚-v2.png")
        self.ps("美术", "set-asset", "C00002-A", "--set", "detail=角色/x.txt", ok=False)

    def test_status_transitions(self):
        self.put("角色/C00002-A-苏晚.png")
        self.ps("美术", "set-asset", "C00002-A", "--set", "status=generated")
        r = self.ps("美术", "set-asset", "C00002-A", "--set", "status=approved", ok=False)
        self.assertIn("总导演", r.stderr)
        self.ps("总导演", "set-asset", "P00001", "--set", "status=approved", ok=False)   # planned 不能直接通过
        self.ps("总导演", "set-asset", "P00001", "--set", "status=generated", ok=False)  # 总导演不代写 generated
        self.ps("总导演", "set-asset", "C00002-A", "--set", "status=approved")
        self.ps("美术", "set-asset", "C00002-A", "--set", "status=failed", ok=False)     # 已通过的产出者不能改
        self.ps("总导演", "set-asset", "C00002-A", "--set", "status=planned")
        self.ps("编剧", "set-asset", "C00002-A", "--set", "status=generated", ok=False)
        self.ps("美术", "set-asset", "C00002-A", "--set", "status=bogus", ok=False)

    def test_ids_are_immutable_and_unknown_fields_refused(self):
        self.ps("编剧", "set-asset", "C00002-A", "--set", "type=prop", ok=False)
        r = self.ps("编剧", "set-asset", "C00002-A", "--set", "colour=red", ok=False)
        self.assertIn("归属表", r.stderr)
        self.ps("编剧", "set-clip", "EP001/Clip00001", "--set", "episode=EP002", ok=False)

    def test_add_clip_after_keeps_play_order(self):
        clip = {"id": "EP001/Clip00004", "episode": "EP001", "duration": 5, "assets": ["S00001"],
                "prompt": "单集制作/EP001/视频_Clip00004.prompt.txt", "video": "单集制作/EP001/视频_Clip00004.mp4",
                "continue_from": None}
        self.ps("编剧", "add-clip", "--json", json.dumps(clip, ensure_ascii=False), "--after", "EP001/Clip00001")
        self.assertEqual([c["id"] for c in self.load("clips.json")],
                         ["EP001/Clip00001", "EP001/Clip00004", "EP001/Clip00002", "EP001/Clip00003"])
        self.ps("编剧", "add-clip", "--json", json.dumps(clip, ensure_ascii=False), ok=False)  # 重复

    def test_draft_and_final_are_separate(self):
        self.put("单集制作/EP001/试生成/视频_Clip00001.mp4")
        self.ps("摄影", "set-clip", "EP001/Clip00001", "--set", "draft.status=generated",
                "--set", "draft.ending_frame=单集制作/EP001/尾帧/Clip00001-draft.png")
        c = self.clip("EP001/Clip00001")
        self.assertEqual((c["draft"]["status"], c["status"]), ("generated", "planned"))
        self.ps("摄影", "set-clip", "EP001/Clip00001", "--set", "draft.status=approved", ok=False)
        self.ps("总导演", "set-clip", "EP001/Clip00001", "--set", "draft.status=approved")
        self.assertEqual(self.clip("EP001/Clip00001")["draft"]["status"], "approved")
        self.ps("编剧", "set-clip", "EP001/Clip00001", "--set", "draft.video=x.mp4", ok=False)

    def test_narration_fields_split_between_writer_and_editor(self):
        cid = "EP001/Clip00003"
        r = self.ps("剪辑", "set-clip", cid, "--set", "narration.Clip00003-1.narration_duration=4.2")
        self.assertEqual(json.loads(r.stdout)["changes"], {"narration.Clip00003-1.narration_duration": [3.6, 4.2]})
        self.assertEqual(self.clip(cid)["narration"][0]["narration_duration"], 4.2)
        self.ps("剪辑", "set-clip", cid, "--set", "duration=12", ok=False)
        self.ps("剪辑", "set-clip", cid, "--set", "narration.Clip00003-1.text=改词", ok=False)
        self.ps("编剧", "set-clip", cid, "--set", "narration.Clip00003-1.audio=x.wav", ok=False)
        self.ps("编剧", "set-clip", cid, "--set", "narration.Clip00003-1.text=那一夜，雪很大。")
        self.ps("编剧", "set-clip", cid, "--set", "narration.Clip00009-1.text=x", ok=False)  # 没有这一条
        # 编剧加一条新旁白（整体替换列表，不碰已有条目里剪辑的字段）
        items = self.clip(cid)["narration"] + [{"id": "Clip00003-2", "start": 7, "reserved": 2, "text": "他推开了门。"}]
        self.ps("编剧", "set-clip", cid, "--json", json.dumps({"narration": items}, ensure_ascii=False))
        # 编剧整条删除旁白可以，剪辑不行
        self.ps("剪辑", "set-clip", cid, "--json", json.dumps({"narration": items[1:]}, ensure_ascii=False), ok=False)
        self.ps("编剧", "set-clip", cid, "--json", json.dumps({"narration": items[1:]}, ensure_ascii=False))
        self.assertEqual([n["id"] for n in self.clip(cid)["narration"]], ["Clip00003-2"])

    def test_voices_registry(self):
        self.put("音色/C00001.wav")
        voice = {"character": "C00001", "file": "音色/C00001.wav", "source_clip": "EP001/Clip00001",
                 "start": 4.1, "end": 8.0, "text": "这扇门，我一定要推开。", "forms": ["C00001-A"]}
        self.ps("编剧", "add-voice", "--json", json.dumps(voice, ensure_ascii=False), ok=False)
        self.ps("摄影", "add-voice", "--json", json.dumps(voice, ensure_ascii=False))
        self.assertEqual(self.load("voices.json")[0]["status"], "generated")
        self.ps("摄影", "set-voice", "C00001", "--set", "status=approved", ok=False)
        self.ps("总导演", "set-voice", "C00001", "--set", "status=approved")
        self.ps("摄影", "set-voice", "C00001", "--set", "end=7.5")

    def test_remove_only_by_director(self):
        self.ps("编剧", "remove-asset", "P00001", ok=False)
        self.ps("总导演", "remove-asset", "P00001")
        self.assertNotIn("P00001", [a["id"] for a in self.load("assets.json")])

    def test_written_files_use_lf(self):
        self.put("角色/C00002-A-苏晚.png")
        self.ps("美术", "set-asset", "C00002-A", "--set", "status=generated")
        for rel in ("assets.json", "logs/state-changes.jsonl", "project.json", "大纲.txt"):
            self.assertNotIn(b"\r\n", (self.root / rel).read_bytes(), rel)

    def test_backup_log_and_noop(self):
        self.put("角色/C00002-A-苏晚.png")
        self.ps("美术", "set-asset", "C00002-A", "--set", "status=generated")
        self.assertEqual(len(list((self.root / ".backup").glob("assets.json.*"))), 1)
        log = (self.root / "logs/state-changes.jsonl").read_text(encoding="utf-8").splitlines()
        entry = json.loads(log[-1])
        self.assertEqual((entry["role"], entry["target"], entry["changes"]["status"]),
                         ("美术", "C00002-A", ["planned", "generated"]))
        r = self.ps("美术", "set-asset", "C00002-A", "--set", "status=generated")  # 没有变化
        self.assertEqual(json.loads(r.stdout)["changes"], {})
        self.assertEqual(len((self.root / "logs/state-changes.jsonl").read_text(encoding="utf-8").splitlines()), len(log))

    def test_parallel_writers_do_not_lose_updates(self):
        jobs = [("摄影", "EP001/Clip00001", "task_dir=tasks/a/attempt-001"),
                ("摄影", "EP001/Clip00002", "task_dir=tasks/b/attempt-001"),
                ("剪辑", "EP001/Clip00003", "narration.Clip00003-1.narration_duration=3.9"),
                ("编剧", "EP001/Clip00001", "continuity_reason=开场"),
                ("编剧", "EP001/Clip00002", "duration=7"),
                ("摄影", "EP001/Clip00003", "ending_frame=单集制作/EP001/尾帧/Clip00003.png")]
        with ThreadPoolExecutor(len(jobs)) as ex:
            list(ex.map(lambda j: self.ps(j[0], "set-clip", j[1], "--set", j[2]), jobs))
        c1, c2, c3 = (self.clip(f"EP001/Clip0000{i}") for i in (1, 2, 3))
        self.assertEqual((c1["task_dir"], c1["continuity_reason"]), ("tasks/a/attempt-001", "开场"))
        self.assertEqual((c2["task_dir"], c2["duration"]), ("tasks/b/attempt-001", 7))
        self.assertEqual((c3["narration"][0]["narration_duration"], c3["ending_frame"]),
                         (3.9, "单集制作/EP001/尾帧/Clip00003.png"))

    CLIP9 = {"id": "EP001/Clip00009", "episode": "EP001", "duration": 5, "assets": [], "prompt": "单集制作/EP001/a.txt",
             "video": "单集制作/EP001/视频_Clip00009.mp4", "continue_from": None}

    def test_create_cannot_preapprove_draft(self):
        bad = {**self.CLIP9, "draft": {"status": "approved"}}
        r = self.ps("编剧", "add-clip", "--json", json.dumps(bad, ensure_ascii=False), ok=False)
        self.assertIn("draft.status", r.stderr)
        self.ps("编剧", "add-clip", "--json", json.dumps({**self.CLIP9, "draft": {"task_dir": "tasks/x"}}), ok=False)
        good = {**self.CLIP9, "draft": {"video": "单集制作/EP001/试生成/视频_Clip00009.mp4", "status": "planned"}}
        self.ps("编剧", "add-clip", "--json", json.dumps(good, ensure_ascii=False))

    def test_path_and_type_checks_on_write(self):
        for bad in ("image=C:/x.png", "image=../外面.png", "image=/x.png", "image="):
            self.ps("美术", "set-asset", "C00002-A", "--set", bad, ok=False)
        for bad in ("duration=八", "duration=3", "duration=16", "duration=6.5", "needs_draft=yes", "assets=C00001-A"):
            self.ps("编剧", "set-clip", "EP001/Clip00001", "--set", bad, ok=False)
        self.ps("剪辑", "set-clip", "EP001/Clip00003", "--set", "narration.Clip00003-1.narration_duration=-1", ok=False)
        self.ps("编剧", "set-clip", "EP001/Clip00001", "--set", "needs_draft=true")
        self.assertIs(self.clip("EP001/Clip00001")["needs_draft"], True)

    def test_json_file_input(self):
        f = Path(self.tmp.name) / "clip.json"
        f.write_text(json.dumps(self.CLIP9, ensure_ascii=False), encoding="utf-8-sig")   # 带 BOM 也能读
        self.ps("编剧", "add-clip", "--json-file", f)
        self.assertEqual(self.clip("EP001/Clip00009")["duration"], 5)
        self.ps("编剧", "add-clip", "--json-file", f, "--json", "{}", ok=False)

    def test_writes_survive_concurrent_readers(self):
        import threading
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    (self.root / "clips.json").read_text(encoding="utf-8")
                except OSError:
                    pass
        threads = [threading.Thread(target=reader) for _ in range(2)]
        for t in threads:
            t.start()
        try:
            for i in range(15):
                self.ps("摄影", "set-clip", "EP001/Clip00001", "--set", f"task_dir=tasks/r{i}/attempt-001")
        finally:
            stop.set()
            for t in threads:
                t.join()
        self.assertEqual(self.clip("EP001/Clip00001")["task_dir"], "tasks/r14/attempt-001")
        self.assertEqual(len(list((self.root / ".backup").glob("clips.json.*"))), 15)
        self.assertEqual(len((self.root / "logs/state-changes.jsonl").read_text(encoding="utf-8").splitlines()), 15)

    def test_log_failure_rolls_back_change(self):
        self.put("角色/C00002-A-苏晚.png")
        (self.root / "logs/state-changes.jsonl").mkdir(parents=True)   # 让日志写不进去
        before = (self.root / "assets.json").read_bytes()
        r = self.ps("美术", "set-asset", "C00002-A", "--set", "status=generated", ok=False)
        self.assertIn("没有生效", r.stderr)
        self.assertEqual((self.root / "assets.json").read_bytes(), before)
        self.assertEqual(list((self.root / ".backup").glob("assets.json.*")), [])

    def test_project_can_disable_extra_manifest_backups(self):
        project=self.load('project.json');project['storage']={'manifest_backups':False};self.save('project.json',project)
        self.ps('摄影','set-clip','EP001/Clip00001','--set','task_dir=tasks/new/attempt-001')
        self.assertFalse((self.root/'.backup').exists())
        self.assertTrue((self.root/'logs/state-changes.jsonl').is_file())

    def test_disabled_backups_preserve_old_backups(self):
        project=self.load('project.json');project['storage']={'manifest_backups':False};self.save('project.json',project)
        folder=self.root/'.backup';folder.mkdir()
        for i in range(55):(folder/f'clips.json.old{i:03d}').write_bytes(b'old user backup')
        before={p.name:p.read_bytes() for p in folder.iterdir()}
        self.ps('摄影','set-clip','EP001/Clip00001','--set','task_dir=tasks/new/attempt-001')
        self.assertEqual(before,{p.name:p.read_bytes() for p in folder.iterdir()})

    def test_disabled_backup_log_failure_restores_exact_original(self):
        project=self.load('project.json');project['storage']={'manifest_backups':False};self.save('project.json',project)
        path=self.root/'clips.json';before=path.read_bytes()
        (self.root/'logs/state-changes.jsonl').mkdir(parents=True)
        self.ps('摄影','set-clip','EP001/Clip00001','--set','task_dir=tasks/new/attempt-001',ok=False)
        self.assertEqual(before,path.read_bytes())
        self.assertFalse((self.root/'.backup').exists())

    def test_invalid_backup_policy_is_refused_without_write(self):
        project=self.load('project.json');project['storage']={'manifest_backups':'false'};self.save('project.json',project)
        path=self.root/'clips.json';before=path.read_bytes()
        self.ps('摄影','set-clip','EP001/Clip00001','--set','task_dir=tasks/new/attempt-001',ok=False)
        self.assertEqual(before,path.read_bytes())

    def test_lock_times_out_instead_of_hanging(self):
        import threading, time, pipeline_state
        lock = str(self.root / ".pipeline_state.lock")
        held, release = threading.Event(), threading.Event()

        def holder():
            with pipeline_state.exclusive_lock(lock):
                held.set()
                release.wait(5)
        t = threading.Thread(target=holder)
        t.start()
        held.wait(5)
        t0 = time.monotonic()
        try:
            with self.assertRaises(pipeline_state.Refused):
                with pipeline_state.exclusive_lock(lock, timeout=0.3):
                    pass
        finally:
            release.set()
            t.join()
        self.assertLess(time.monotonic() - t0, 3)

    # ---- A1：内容变了自动降级；指纹
    def approve_scene(self):
        self.ps("美术", "set-asset", "S00001", "--set", "status=failed")       # 样例里是 generated，走一遍重新生成
        self.ps("美术", "set-asset", "S00001", "--set", "status=generated")
        self.ps("总导演", "set-asset", "S00001", "--set", "status=approved")

    def test_generated_records_fingerprint_and_needs_file(self):
        self.ps("美术", "set-asset", "P00001", "--set", "status=generated", ok=False)   # 图还没有
        self.put("道具/P00001-青锋剑.png")
        self.ps("美术", "set-asset", "P00001", "--set", "status=generated")
        self.assertEqual(set(self.asset("P00001")["fingerprint"]), {"image"})
        self.ps("美术", "set-asset", "P00001", "--set", "fingerprint={}", ok=False)       # 指纹只能由系统写

    def test_changing_output_of_approved_asset_needs_new_review(self):
        self.approve_scene()
        self.put("场景/S00001-雪夜山门-v2.png", b"new image")
        r = self.ps("美术", "set-asset", "S00001", "--set", "image=场景/S00001-雪夜山门-v2.png")
        self.assertEqual(json.loads(r.stdout)["auto"], ["fingerprint", "status"])
        self.assertEqual(self.asset("S00001")["status"], "generated")
        self.ps("总导演", "set-asset", "S00001", "--set", "status=approved")

    def test_overwritten_file_blocks_approval(self):
        self.ps("美术", "set-asset", "S00001", "--set", "status=failed")
        self.ps("美术", "set-asset", "S00001", "--set", "status=generated")
        self.put("场景/S00001-雪夜山门.png", b"silently replaced")   # 同一路径被覆盖
        r = self.ps("总导演", "set-asset", "S00001", "--set", "status=approved", ok=False)
        self.assertIn("被改过", r.stderr)
        self.assertTrue(any("指纹" in e for e in self.validate()["errors"]))

    def test_writer_change_sends_asset_and_clip_back_to_planned(self):
        self.approve_scene()
        self.ps("编剧", "set-asset", "S00001", "--set", "detail=场景/S00001-新详情.txt")
        a = self.asset("S00001")
        self.assertEqual(a["status"], "planned")
        self.assertNotIn("fingerprint", a)
        self.put("单集制作/EP001/视频_Clip00001.mp4")
        self.put("单集制作/EP001/试生成/视频_Clip00001.mp4")
        self.ps("摄影", "set-clip", "EP001/Clip00001", "--set", "status=generated", "--set", "draft.status=generated")
        self.ps("编剧", "set-clip", "EP001/Clip00001", "--set", "duration=9", "--set", "shots=[]")
        c = self.clip("EP001/Clip00001")
        self.assertEqual((c["status"], c["draft"]["status"]), ("planned", "planned"))
        self.ps("编剧", "set-clip", "EP001/Clip00002", "--set", "continuity_reason=换个说法")   # 不影响画面的字段不降级
        self.assertEqual(self.clip("EP001/Clip00002")["status"], "planned")

    def test_changing_approved_voice_file_needs_new_review(self):
        self.put("音色/C00001.wav")
        voice = {"character": "C00001", "file": "音色/C00001.wav", "source_clip": "EP001/Clip00001",
                 "start": 4.0, "end": 8.0, "text": "这扇门", "forms": ["C00001-A"]}
        self.ps("摄影", "add-voice", "--json", json.dumps(voice, ensure_ascii=False))
        self.ps("总导演", "set-voice", "C00001", "--set", "status=approved")
        self.ps("摄影", "set-voice", "C00001", "--set", "end=7.6")
        self.assertEqual(self.load("voices.json")[0]["status"], "generated")

    # ---- A2：旁白改词，旧配音作废
    def test_narration_text_change_voids_old_audio(self):
        r = self.ps("编剧", "set-clip", "EP001/Clip00003", "--set", "narration.Clip00003-1.text=那一夜，风很冷。")
        self.assertIn("narration.Clip00003-1.audio", json.loads(r.stdout)["auto"])
        n = self.clip("EP001/Clip00003")["narration"][0]
        self.assertNotIn("audio", n)
        self.assertNotIn("narration_duration", n)
        self.ps("编剧", "set-clip", "EP001/Clip00003", "--set", "narration.Clip00003-1.start=1")   # 只改时间不作废
        self.assertEqual(self.clip("EP001/Clip00003")["narration"][0]["start"], 1)

    # ---- A3：总导演清空试生成记录
    def test_director_can_clear_draft(self):
        self.ps("摄影", "set-clip", "EP001/Clip00002", "--json", '{"draft": null}', ok=False)
        self.ps("总导演", "set-clip", "EP001/Clip00002", "--json", '{"draft": null}')
        self.assertIsNone(self.clip("EP001/Clip00002")["draft"])

    # ---- A4：直接用试生成作为定稿
    def test_final_from_draft_flag_owned_by_director(self):
        self.ps("摄影", "set-clip", "EP001/Clip00003", "--set", "final_from_draft=true", ok=False)
        self.ps("总导演", "set-clip", "EP001/Clip00003", "--set", "final_from_draft=true")
        self.assertIs(self.clip("EP001/Clip00003")["final_from_draft"], True)

    def test_show_is_read_only_and_role_required_for_writes(self):
        r = self.ps(None, "show", "clips", "EP001/Clip00002")
        self.assertEqual(json.loads(r.stdout)["continue_from"], "EP001/Clip00001")
        self.ps(None, "set-clip", "EP001/Clip00002", "--set", "duration=7", ok=False)


class ValidateTest(Base):
    def errors(self, stage="plan"):
        return self.validate(stage)["errors"]

    def edit(self, name, fn):
        data = self.load(name)
        fn(data)
        self.save(name, data)

    def test_sample_passes_plan_but_not_assets(self):
        res = self.validate("plan")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["warnings"], [])
        self.assertFalse(self.validate("assets")["ok"])

    def test_v1_errors_still_reported(self):
        self.edit("clips.json", lambda d: d[1].update(continue_from="EP001/Clip00099"))
        self.assertTrue(any("Continuity" in e for e in self.errors()))

    def test_duration_must_be_integer_4_to_15(self):
        for bad in (4.5, 3, 16, True):
            self.edit("clips.json", lambda d: d[0].update(duration=bad, shots=[]))
            self.assertTrue(any("duration" in e for e in self.errors()), bad)

    def test_project_fields(self):
        self.edit("project.json", lambda d: d.update(genre="romance"))
        self.assertTrue(any("genre" in e for e in self.errors()))
        self.edit("project.json", lambda d: d.update(genre="urban", ratio="adaptive"))
        self.assertTrue(any("ratio" in e for e in self.errors()))
        self.edit("project.json", lambda d: (d.update(ratio="9:16", publish=True), d["post"]["aigc_label"].update(visible=False)))
        self.assertTrue(any("AI 标识" in e for e in self.errors()))
        self.edit("project.json", lambda d: (d["post"]["aigc_label"].update(visible=True), d.update(video_model="x")))
        res = self.validate()
        self.assertTrue(res["ok"], res)
        self.assertTrue(any("video_model" in w for w in res["warnings"]))

    def test_v1_project_only_gets_v1_checks(self):
        self.edit("project.json", lambda d: d.pop("schema_version"))
        self.edit("clips.json", lambda d: d[0].update(duration=4.5, shots=[]))   # v2 才拦
        res = self.validate()
        self.assertTrue(res["ok"], res)
        self.assertTrue(any("schema_version 2" in w for w in res["warnings"]))

    def test_generated_items_need_files(self):
        self.edit("clips.json", lambda d: d[0]["draft"].update(status="generated"))
        self.assertTrue(any("draft.video" in e for e in self.errors()))
        self.edit("clips.json", lambda d: (d[0]["draft"].update(status="planned"), d[1].update(status="generated")))
        self.assertTrue(any("Clip00002.video" in e for e in self.errors()))
        self.edit("clips.json", lambda d: d[1].update(status="planned"))
        (self.root / "角色/C00001-A-林舟.sheet.png").unlink()
        self.edit("assets.json", lambda d: d[0].update(status="approved"))
        self.assertTrue(any("sheet" in e for e in self.errors()))

    def test_narration_rules(self):
        self.edit("clips.json", lambda d: d[2]["narration"][0].update(start=8))
        self.assertTrue(any("超过片段时长" in e for e in self.errors()))
        self.edit("clips.json", lambda d: d[2]["narration"][0].update(start=2, narration_duration=4.5))
        res = self.validate()
        self.assertTrue(res["ok"])
        self.assertTrue(any("超出预留" in w and "1 秒以上" not in w for w in res["warnings"]))
        self.edit("clips.json", lambda d: d[2]["narration"][0].update(narration_duration=5.5))
        self.assertTrue(any("1 秒以上" in w for w in self.validate()["warnings"]))
        self.edit("clips.json", lambda d: d[2]["narration"][0].update(id="Clip00001-1"))
        self.assertTrue(any("Clip00003-序号" in e for e in self.errors()))

    def test_voice_anchor_and_voices(self):
        self.edit("clips.json", lambda d: d[0].update(voice_anchor=["C00009"]))
        self.assertTrue(any("voice_anchor" in e for e in self.errors()))
        self.edit("clips.json", lambda d: d[0].update(voice_anchor=["C00001"]))
        voices = [{"character": "C00001", "file": "音色/C00001.wav", "source_clip": "EP001/Clip00001", "start": 1.0,
                   "end": 7.0, "text": "这扇门", "status": "planned", "forms": ["C00002-A"]}]
        self.save("voices.json", voices)
        res = self.validate()
        self.assertTrue(any("forms" in e for e in res["errors"]))
        self.assertTrue(any("模板时长" in w for w in res["warnings"]))
        voices[0].update(forms=["C00001-A"], end=5.0, status="approved")
        self.save("voices.json", voices)
        self.assertTrue(any("voices[C00001].file" in e for e in self.errors()))
        make_sample_project.silent_wav(self.root / "音色/C00001.wav", 4.4)
        self.assertTrue(self.validate()["ok"])

    def test_voice_anchor_accepts_voice_changing_form(self):
        self.edit("assets.json", lambda d: d.append({**d[0], "id": "C00001-C", "name": "林舟-老年", "sheet": None}
                                                      | {"status": "planned"}))
        self.edit("assets.json", lambda d: d[-1].pop("sheet"))
        self.edit("clips.json", lambda d: d[1].update(voice_anchor=["C00001-C"], assets=d[1]["assets"] + ["C00001-C"]))
        self.assertTrue(self.validate()["ok"], self.validate())
        self.edit("clips.json", lambda d: d[1].update(voice_anchor=["C00001-Z"]))
        self.assertTrue(any("C00001-Z" in e for e in self.errors()))

    def test_assemble_requires_narration_ready(self):
        self.edit("clips.json", lambda d: (d[2]["narration"][0].pop("audio"), d[2]["narration"][0].pop("narration_duration")))
        self.assertTrue(self.validate("plan")["ok"])
        errs = self.errors("assemble")
        self.assertTrue(any("缺 audio" in e for e in errs))
        self.assertTrue(any("narration_duration" in e for e in errs))

    def finish_ep001(self):
        """把 EP001 做成"可以合成"：EP001 用到的资产和片段都已通过、文件都在。"""
        for rel in ("角色/C00002-A-苏晚.png", "角色/C00002-A-苏晚.prompt.txt", "道具/P00001-青锋剑.png",
                    "道具/P00001-青锋剑.prompt.txt", "角色/C00001-A-林舟.prompt.txt"):
            self.put(rel)
        self.edit("assets.json", lambda d: [a.update(status="approved") for a in d])
        for i in (1, 2, 3):
            self.put(f"单集制作/EP001/视频_Clip0000{i}.mp4")
            self.put(f"单集制作/EP001/试生成/视频_Clip0000{i}.mp4")
        self.edit("clips.json", lambda d: [(c.update(status="approved"), c["draft"].update(status="approved")) for c in d])

    def test_episode_scoped_stage_checks(self):
        self.finish_ep001()
        self.assertTrue(self.validate("assemble")["ok"], self.validate("assemble"))
        # 加第二集：新资产还没做，片段还没生成
        self.edit("assets.json", lambda d: d.append({"id": "S00002", "type": "scene", "name": "藏经阁",
                                                    "detail": "场景/S00001-雪夜山门.txt", "prompt": "场景/S00002.prompt.txt",
                                                    "image": "场景/S00002.png", "status": "planned"}))
        self.edit("clips.json", lambda d: d.append({"id": "EP002/Clip00001", "episode": "EP002", "duration": 6,
                                                   "assets": ["S00002"], "prompt": "单集制作/EP002/视频_Clip00001.prompt.txt",
                                                   "video": "单集制作/EP002/视频_Clip00001.mp4", "continue_from": None,
                                                   "status": "planned"}))
        self.assertFalse(self.validate("assemble")["ok"])                     # 整部作品：EP002 没做完
        res = self.validate("assemble", "EP001")
        self.assertTrue(res["ok"], res)                                       # 只看 EP001：可以合成
        errs = self.validate("assemble", "EP002")["errors"]
        self.assertTrue(any("EP002/Clip00001" in e for e in errs))
        self.assertTrue(any("S00002" in e for e in errs))

    def test_final_from_draft_rules(self):
        self.finish_ep001()
        self.edit("clips.json", lambda d: d[0].update(final_from_draft=True))
        self.assertTrue(any("指向试生成文件" in e for e in self.errors("assemble")))
        self.edit("clips.json", lambda d: d[0].update(video=d[0]["draft"]["video"]))
        res = self.validate("assemble")
        self.assertTrue(res["ok"], res)
        self.assertTrue(any("480P" in w for w in res["warnings"]))
        self.edit("clips.json", lambda d: d[0]["draft"].update(status="generated"))
        self.assertTrue(any("试生成本身要先通过" in e for e in self.errors("assemble")))

    def test_budget_rules(self):
        self.edit("approvals/budget.json", lambda d: d[0].update(used=200))
        self.assertTrue(any("超过额度" in e for e in self.errors()))
        self.edit("approvals/budget.json", lambda d: (d[0].update(used=0), d.append(dict(d[0]))))
        self.assertTrue(any("重复" in e for e in self.errors()))

    def test_short_mode_wants_needs_draft(self):
        self.edit("project.json", lambda d: d.update(mode="short"))
        res = self.validate()
        self.assertTrue(res["ok"])
        self.assertEqual(sum("needs_draft" in w for w in res["warnings"]), 3)
        self.edit("clips.json", lambda d: [c.update(needs_draft=False) for c in d])
        self.assertEqual(self.validate()["warnings"], [])


import shutil


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg 不在 PATH 里")
class EndingFrameTest(Base):
    def video(self, rel, color):
        f = self.root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        # 前半秒黑、后半秒指定颜色：抽到的必须是最后一帧的颜色
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=32x32:d=0.5", "-f", "lavfi",
                        "-i", f"color=c={color}:s=32x32:d=0.5", "-filter_complex", "[0][1]concat=n=2:v=1",
                        "-pix_fmt", "yuv420p", str(f)], check=True)

    def frame_rgb(self, rel):
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.root / rel), "-vf", "scale=1:1", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
        return tuple(r.stdout[:3])

    def test_draft_and_final_frames_are_separate(self):
        self.video("单集制作/EP001/试生成/视频_Clip00001.mp4", "red")
        r = run("ending_frame.py", "--project-root", self.root, "--clip-id", "EP001/Clip00002", "--pass", "draft")
        out = json.loads(r.stdout)
        self.assertEqual(out["ending_frame"], "单集制作/EP001/尾帧/Clip00001-draft.png")
        self.assertGreater(self.frame_rgb(out["ending_frame"])[0], 150)        # 最后一帧是红的，不是开头的黑
        self.assertTrue((self.root / "单集制作/EP001/承接报告/Clip00002-draft.json").is_file())
        r = run("ending_frame.py", "--project-root", self.root, "--clip-id", "EP001/Clip00002", "--pass", "final", ok=False)
        self.assertIn("不存在", r.stderr)                                       # 定稿还没有，不能拿试生成顶替
        self.video("单集制作/EP001/视频_Clip00001.mp4", "blue")
        out = json.loads(run("ending_frame.py", "--project-root", self.root, "--clip-id", "EP001/Clip00002",
                             "--pass", "final").stdout)
        self.assertEqual(out["ending_frame"], "单集制作/EP001/尾帧/Clip00001.png")
        self.assertGreater(self.frame_rgb(out["ending_frame"])[2], 150)
        self.assertTrue(out["warnings"])                                        # 定稿还是 planned，要提醒

    def test_clip_without_continuity_needs_no_frame(self):
        out = json.loads(run("ending_frame.py", "--project-root", self.root, "--clip-id", "EP001/Clip00001",
                             "--pass", "draft").stdout)
        self.assertIsNone(out["ending_frame"])

    def test_existing_tail_and_report_are_preserved(self):
        self.video("单集制作/EP001/试生成/视频_Clip00001.mp4", "red")
        args=("--project-root",self.root,"--clip-id","EP001/Clip00002","--pass","draft")
        result=json.loads(run("ending_frame.py",*args).stdout)
        frame=self.root/result['ending_frame']
        report=self.root/'单集制作/EP001/承接报告/Clip00002-draft.json'
        before=(frame.read_bytes(),report.read_bytes())
        self.assertIn('已存在',run("ending_frame.py",*args,ok=False).stderr)
        self.assertEqual(before,(frame.read_bytes(),report.read_bytes()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
