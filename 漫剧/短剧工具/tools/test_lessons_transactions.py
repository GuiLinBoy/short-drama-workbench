"""Batch 3 regressions; copied templates and Git repositories live in scratch."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
SCRIPT=HERE/'lessons.py'
ROOT=HERE.parent
sys.path.insert(0,str(HERE))
from test_lessons import GENRE_MD, PITFALLS_MD, L_HEADER
import lessons


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manju=Path(self.tmp.name)/'漫剧'
        self.tools=self.manju/'短剧工具'
        for folder in ('genres','skills/nexorai-image','skills/minimax-h3-video/references'):
            (self.tools/folder).mkdir(parents=True,exist_ok=True)
        self.genre=self.tools/'genres/3d-guoman.md'
        self.genre.write_text(GENRE_MD,encoding='utf-8',newline='\n')
        (self.tools/'镜头语言手册.md').write_text('# 手册\n\n## 实战经验\n\n'+L_HEADER+'\n',encoding='utf-8',newline='\n')
        # Use the actual, empty NexorAI section and the actual gapped pitfall table.
        for rel in ('skills/nexorai-image/SKILL.md','skills/minimax-h3-video/references/pitfalls.md'):
            (self.tools/rel).write_bytes((ROOT/rel).read_bytes())
        self.project=self.manju/'作品/A'
        self.project.mkdir(parents=True)
        self.env={**os.environ,'GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_NOSYSTEM':'1',
                  'LESSONS_TOOLS':str(self.tools),'GIT_AUTHOR_NAME':'test',
                  'GIT_AUTHOR_EMAIL':'test@example.invalid','GIT_COMMITTER_NAME':'test',
                  'GIT_COMMITTER_EMAIL':'test@example.invalid'}
        self.git(self.manju,'init','-q')
        (self.manju/'.gitignore').write_text('短剧工具/\n',encoding='utf-8')
        self.git(self.manju,'add','.gitignore')
        self.git(self.manju,'commit','-qm','init data')
        self.git(self.tools,'init','-q')
        self.git(self.tools,'add','.')
        self.git(self.tools,'commit','-qm','init tools')

    def git(self,root,*args,check=True):
        return subprocess.run(['git','-C',str(root),*map(str,args)],env=self.env,
                              capture_output=True,check=check)

    def cli(self,*args,ok=True):
        p=subprocess.run([sys.executable,'-I',str(SCRIPT),*map(str,args)],env=self.env,
                         capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(p.returncode==0,ok,p.stdout+'\n'+p.stderr)
        return p

    def add(self,*extra,phenomenon='一个独立的测试现象'):
        return self.cli('add','--target','genre','--genre','3d-guoman','--source','SIMULATED/A',
                        '--phenomenon',phenomenon,'--fix','测试规避方法','--confirm','SIMULATED approval',*extra)

    def test_default_write_does_not_commit_either_repository(self):
        before=[self.git(p,'rev-parse','HEAD').stdout for p in (self.manju,self.tools)]
        self.add()
        self.assertEqual(before,[self.git(p,'rev-parse','HEAD').stdout for p in (self.manju,self.tools)])

    def test_real_empty_nexorai_template_accepts_add(self):
        self.cli('add','--target','nexorai','--source','SIMULATED/A','--phenomenon','空模板兼容测试',
                 '--fix','测试方法','--confirm','SIMULATED approval')
        text=(self.tools/'skills/nexorai-image/SKILL.md').read_text(encoding='utf-8')
        self.assertIn('| L001 |',text)

    def test_wrong_section_is_refused_without_writing(self):
        self.genre.write_text(GENRE_MD.replace('## 实战经验（复盘沉淀）','## 正文规则'),encoding='utf-8')
        before=self.genre.read_bytes()
        self.cli('add','--target','genre','--genre','3d-guoman','--source','s',
                 '--phenomenon','章节拒绝测试','--fix','f','--confirm','SIMULATED',ok=False)
        self.assertEqual(before,self.genre.read_bytes())
        self.assertFalse((self.manju/'经验库.md').exists())

    def test_project_experience_can_be_retracted(self):
        self.cli('add','--target','project','--project',self.project,'--source','s',
                 '--phenomenon','作品撤销测试','--fix','f','--confirm','SIMULATED')
        self.cli('retract','L001','--confirm','SIMULATED retract')
        self.assertNotIn('| L001 |',(self.project/'作品约定.md').read_text(encoding='utf-8'))

    def test_pitfall_status_retraction_restores_exact_row(self):
        path=self.tools/'skills/minimax-h3-video/references/pitfalls.md'
        before=path.read_bytes()
        self.cli('pitfall','--row','26','--status','无效','--source','SIMULATED','--confirm','SIMULATED')
        self.cli('retract','L001','--confirm','SIMULATED retract')
        self.assertEqual(before,path.read_bytes())

    def test_pitfall_promotion_requires_explicit_evidence(self):
        self.cli('pitfall','--row','26','--status','已证实（2次）','--source','SIMULATED',
                 '--confirm','SIMULATED',ok=False)

    def test_update_and_retract_restore_previous_revision(self):
        self.add()
        before=self.genre.read_bytes()
        result=json.loads(self.cli('update','L001','--fix','新的测试方法','--count','2',
                                   '--level','规则','--confirm','SIMULATED update').stdout)
        self.assertIn('新的测试方法',self.genre.read_text(encoding='utf-8'))
        self.cli('retract',result['written'],'--confirm','SIMULATED undo update')
        self.assertEqual(before,self.genre.read_bytes())

    def test_subsequent_edit_prevents_old_retraction(self):
        self.add()
        self.cli('update','L001','--fix','后续测试方法','--confirm','SIMULATED update')
        before=self.genre.read_bytes()
        self.cli('retract','L001','--confirm','SIMULATED old undo',ok=False)
        self.assertEqual(before,self.genre.read_bytes())

    def test_process_crash_after_target_write_recovers_once(self):
        p=self.crash('target')
        self.assertEqual(p.returncode,37,p.stderr)
        self.assertFalse((self.manju/'经验库.md').exists())
        self.cli('recover','--confirm','SIMULATED recover')
        self.cli('recover','--confirm','SIMULATED repeat recover')
        self.assertEqual(self.genre.read_text(encoding='utf-8').count('| L001 |'),1)
        self.assertEqual((self.manju/'经验库.md').read_text(encoding='utf-8').count('| L001 |'),1)
        self.assertEqual(json.loads((self.manju/'经验操作/L001.json').read_text(encoding='utf-8'))['phase'],'committed')

    def crash(self,mode,*args):
        command=[sys.executable,'-I',__file__,'--worker',mode,*map(str,args)]
        return subprocess.run(command,env=self.env,capture_output=True,text=True,encoding='utf-8')

    def test_process_crash_after_both_writes_does_not_repeat_rows(self):
        p=self.crash('journal')
        self.assertEqual(p.returncode,38,p.stderr)
        before=self.genre.read_bytes(),(self.manju/'经验库.md').read_bytes()
        self.cli('recover','--confirm','SIMULATED recover')
        self.assertEqual(before,(self.genre.read_bytes(),(self.manju/'经验库.md').read_bytes()))

    def test_recovery_preserves_intervening_user_edits(self):
        self.crash('target')
        with self.genre.open('a',encoding='utf-8',newline='\n') as f:f.write('\n用户后续内容\n')
        before=self.genre.read_bytes()
        self.cli('recover','--confirm','SIMULATED recover',ok=False)
        self.assertEqual(before,self.genre.read_bytes())
        self.assertFalse((self.manju/'经验库.md').exists())

    def test_parallel_writers_get_distinct_numbers(self):
        processes=[]
        for text in ('甲类型现象','乙类型现象','丙类型现象'):
            args=['add','--target','genre','--genre','3d-guoman','--source','SIMULATED',
                  '--phenomenon',text,'--fix','f','--confirm','SIMULATED']
            processes.append(subprocess.Popen([sys.executable,'-I',str(SCRIPT),*args],env=self.env,
                                               stdout=subprocess.PIPE,stderr=subprocess.PIPE))
        ids=[]
        for p in processes:
            out,err=p.communicate(timeout=60)
            self.assertEqual(p.returncode,0,err.decode('utf-8','replace'))
            ids.append(json.loads(out)['written'])
        self.assertEqual(sorted(ids),['L001','L002','L003'])

    def test_escaped_pipes_and_backslashes_survive_update_and_retract(self):
        self.add(phenomenon='竖杠 | 与路径 C:\\demo\\ 完整保留')
        before=self.genre.read_bytes()
        r=json.loads(self.cli('update','L001','--fix','方法 A | 方法 B \\ end',
                              '--confirm','SIMULATED').stdout)
        self.cli('retract',r['written'],'--confirm','SIMULATED undo')
        self.assertEqual(before,self.genre.read_bytes())
        self.cli('retract','L001','--confirm','SIMULATED retract')

    def test_new_pitfall_retract_removes_only_added_row(self):
        path=self.tools/'skills/minimax-h3-video/references/pitfalls.md'
        before=path.read_bytes()
        r=json.loads(self.cli('pitfall','--new-pitfall','--source','SIMULATED','--phenomenon','新增坑测试',
                              '--fix','f','--confirm','SIMULATED').stdout)
        self.assertEqual(r['pitfall_no'],29)
        self.cli('retract',r['written'],'--confirm','SIMULATED undo')
        self.assertEqual(path.read_bytes(),before)

    def test_retraction_can_itself_be_undone(self):
        self.add()
        before=self.genre.read_bytes()
        undo=json.loads(self.cli('retract','L001','--confirm','SIMULATED retract').stdout)
        self.cli('retract',undo['written'],'--confirm','SIMULATED undo retraction')
        self.assertEqual(before,self.genre.read_bytes())
        ledger=(self.manju/'经验库.md').read_text(encoding='utf-8')
        cells=lessons.cells(next(l for l in ledger.splitlines() if l.startswith('| L001 |')))
        self.assertEqual(cells[5],'观察')

    def test_preview_is_read_only_and_explicit_commit_uses_both_repositories(self):
        self.add()
        before_heads=[self.git(p,'rev-parse','HEAD').stdout for p in (self.manju,self.tools)]
        before=set(self.manju.rglob('*'))
        preview=json.loads(self.cli('commit','L001').stdout)
        self.assertEqual(preview['blockers'],[])
        self.assertEqual(len(preview['repositories']),2)
        self.assertEqual(before,set(self.manju.rglob('*')))
        self.assertEqual(before_heads,[self.git(p,'rev-parse','HEAD').stdout for p in (self.manju,self.tools)])
        result=json.loads(self.cli('commit','L001','--execute','--confirm','SIMULATED Git approval').stdout)
        self.assertEqual(len(result['commits']),2)
        self.assertEqual(self.git(self.tools,'diff','--cached','--name-only').stdout,b'')
        self.assertEqual(self.git(self.manju,'diff','--cached','--name-only').stdout,b'')

    def test_pre_staged_unrelated_changes_are_preserved_and_refused(self):
        self.add()
        unrelated=self.tools/'unrelated.txt'
        unrelated.write_text('user staged work',encoding='utf-8')
        self.git(self.tools,'add','unrelated.txt')
        index=self.git(self.tools,'ls-files','--stage').stdout
        head=self.git(self.tools,'rev-parse','HEAD').stdout
        self.cli('commit','L001','--execute','--confirm','SIMULATED',ok=False)
        self.assertEqual(index,self.git(self.tools,'ls-files','--stage').stdout)
        self.assertEqual(head,self.git(self.tools,'rev-parse','HEAD').stdout)
        self.assertEqual(unrelated.read_text(encoding='utf-8'),'user staged work')

    def test_target_preexisting_modifications_are_not_committed(self):
        with self.genre.open('a',encoding='utf-8',newline='\n') as f:f.write('\n用户既有正文\n')
        self.add()
        before=self.genre.read_bytes()
        self.cli('commit','L001','--execute','--confirm','SIMULATED',ok=False)
        self.assertEqual(before,self.genre.read_bytes())
        self.assertEqual(self.git(self.tools,'rev-list','--count','HEAD').stdout.strip(),b'1')

    def test_project_commit_selects_data_repository_only(self):
        self.cli('add','--target','project','--project',self.project,'--source','s',
                 '--phenomenon','项目提交测试','--fix','f','--confirm','SIMULATED')
        result=json.loads(self.cli('commit','L001','--execute','--confirm','SIMULATED').stdout)
        self.assertEqual(list(result['commits']),[str(self.manju)])
        self.assertEqual(self.git(self.tools,'rev-list','--count','HEAD').stdout.strip(),b'1')

    def test_git_head_advanced_but_index_not_replaced_recovers_without_extra_commit(self):
        self.add()
        p=self.crash('git-index','commit','L001','--execute','--confirm','SIMULATED')
        self.assertEqual(p.returncode,39,p.stderr)
        self.cli('commit','L001','--execute','--confirm','SIMULATED resume')
        self.cli('commit','L001','--execute','--confirm','SIMULATED idempotent resume')
        for root in (self.manju,self.tools):
            self.assertEqual(self.git(root,'rev-list','--count','HEAD').stdout.strip(),b'2')
            self.assertEqual(self.git(root,'diff','--cached','--name-only').stdout,b'')

    def test_fenced_or_duplicate_experience_headings_are_refused(self):
        for text in ('# 正文\n```markdown\n## 实战经验\n'+L_HEADER+'\n```\n',
                     GENRE_MD+'\n## 实战经验\n'+L_HEADER+'\n'):
            self.genre.write_text(text,encoding='utf-8',newline='\n')
            before=self.genre.read_bytes()
            self.cli('add','--target','genre','--genre','3d-guoman','--source','s',
                     '--phenomenon','章节保护测试','--fix','f','--confirm','SIMULATED',ok=False)
            self.assertEqual(self.genre.read_bytes(),before)

    def test_all_normal_targets_support_update_and_retraction(self):
        targets=[['--target','manual'],['--target','nexorai'],['--target','project','--project',self.project]]
        for i,target in enumerate(targets):
            original=json.loads(self.cli('add',*target,'--source','s','--phenomenon',f'{i}号目标独立现象',
                                         '--fix','f','--confirm','SIMULATED').stdout)['written']
            updated=json.loads(self.cli('update',original,'--fix','调整后的方法','--confirm','SIMULATED').stdout)['written']
            self.cli('retract',updated,'--confirm','SIMULATED undo update')
            self.cli('retract',original,'--confirm','SIMULATED undo add')
            self.assertEqual(json.loads(self.cli('list',*target).stdout)['rows'],0)

    def test_invalid_counts_and_missing_pitfall_parameters_never_write(self):
        before=self.genre.read_bytes()
        for count in ('0','-1'):
            self.cli('add','--target','genre','--genre','3d-guoman','--source','s','--phenomenon','次数测试',
                     '--fix','f','--count',count,'--confirm','SIMULATED',ok=False)
        self.cli('pitfall','--new-pitfall','--source','s','--confirm','SIMULATED',ok=False)
        self.assertEqual(self.genre.read_bytes(),before)
        self.assertFalse((self.manju/'经验库.md').exists())

    def test_legacy_record_without_restore_evidence_is_not_guessed(self):
        header=lessons.row(lessons.R_COLS)+'\n|---|---|---|---|---|---|---|---|\n'
        legacy=lessons.row(['L001','2026-11-06','旧来源','pitfalls.md','旧状态修改','观察','旧确认','-'])
        (self.manju/'经验库.md').write_text('# 经验库\n\n'+header+legacy+'\n',encoding='utf-8',newline='\n')
        path=self.tools/'skills/minimax-h3-video/references/pitfalls.md'
        before=path.read_bytes()
        self.cli('retract','L001','--confirm','SIMULATED',ok=False)
        self.assertEqual(path.read_bytes(),before)

    def test_read_only_list_status_and_preview_create_no_files(self):
        before={str(p):p.read_bytes() for p in self.manju.rglob('*') if p.is_file()}
        self.cli('list','--target','nexorai')
        self.cli('status')
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.manju.rglob('*') if p.is_file()})

    def test_outside_project_and_existing_index_lock_are_preserved(self):
        outside=Path(self.tmp.name)/'foreign'
        outside.mkdir()
        self.cli('add','--target','project','--project',outside,'--source','s','--phenomenon','越界测试',
                 '--fix','f','--confirm','SIMULATED',ok=False)
        self.assertFalse((outside/'作品约定.md').exists())
        self.add()
        lock=self.tools/'.git/index.lock'
        lock.write_bytes(b'foreign lock')
        self.cli('commit','L001','--execute','--confirm','SIMULATED',ok=False)
        self.assertEqual(lock.read_bytes(),b'foreign lock')
        self.assertFalse((self.manju/'经验操作/提交回执/L001.json').exists())

    def test_active_hooks_are_not_bypassed_or_run(self):
        self.add()
        hook=self.tools/'.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n',encoding='utf-8',newline='\n')
        result=json.loads(self.cli('commit','L001').stdout)
        self.assertTrue(any('钩子' in x for x in result['blockers']))
        self.cli('commit','L001','--execute','--confirm','SIMULATED',ok=False)
        self.assertEqual(self.git(self.tools,'rev-list','--count','HEAD').stdout.strip(),b'1')

    def test_pitfall_new_duplicate_is_refused(self):
        self.cli('pitfall','--new-pitfall','--source','s','--phenomenon','专门重复检验现象',
                 '--fix','f','--confirm','SIMULATED')
        self.cli('pitfall','--new-pitfall','--source','s','--phenomenon','专门重复检验现象 again',
                 '--fix','f','--confirm','SIMULATED',ok=False)

    def test_existing_official_pitfall_does_not_get_duplicate_new_row(self):
        self.cli('pitfall','--new-pitfall','--source','s','--phenomenon','画面里复制出第二个同样的人',
                 '--fix','f','--confirm','SIMULATED',ok=False)

    def test_retraction_preserves_unrelated_user_edits(self):
        self.add()
        with self.genre.open('a',encoding='utf-8',newline='\n') as f:f.write('\n用户后续的其他说明\n')
        self.cli('retract','L001','--confirm','SIMULATED')
        self.assertIn('用户后续的其他说明',self.genre.read_text(encoding='utf-8'))
        self.assertNotIn('| L001 |',self.genre.read_text(encoding='utf-8'))

    def test_second_repository_failure_reports_partial_and_resumes_without_duplicate_commit(self):
        self.add()
        p=self.crash('git-data','commit','L001','--execute','--confirm','SIMULATED')
        self.assertEqual(p.returncode,40,p.stderr)
        self.assertEqual(self.git(self.tools,'rev-list','--count','HEAD').stdout.strip(),b'2')
        self.assertEqual(self.git(self.manju,'rev-list','--count','HEAD').stdout.strip(),b'1')
        self.cli('commit','L001','--execute','--confirm','SIMULATED resume')
        for root in (self.tools,self.manju):
            self.assertEqual(self.git(root,'rev-list','--count','HEAD').stdout.strip(),b'2')


def worker():
    mode=sys.argv[2]
    sys.argv=[str(SCRIPT),*sys.argv[3:]] if len(sys.argv)>3 else [str(SCRIPT),'add','--target','genre',
             '--genre','3d-guoman','--source','SIMULATED','--phenomenon','中断恢复测试现象','--fix','f','--confirm','SIMULATED']
    if mode=='target':
        original=lessons.atomic_text
        def write_then_crash(path,text):
            original(path,text)
            if path.name!='经验库.md':os._exit(37)
        lessons.atomic_text=write_then_crash
    elif mode=='journal':
        original=lessons.runtime.atomic_json
        def fail_last(path,data):
            if data.get('phase')=='committed':os._exit(38)
            original(path,data)
        lessons.runtime.atomic_json=fail_last
    elif mode=='git-index':
        original=lessons.replace_file
        def crash_index(source,destination):
            if destination.name=='index':os._exit(39)
            original(source,destination)
        lessons.replace_file=crash_index
    elif mode=='git-data':
        original=lessons.prepare_git_group
        def crash_second(store,receipt,g):
            if Path(g['repo'])==store.root:os._exit(40)
            return original(store,receipt,g)
        lessons.prepare_git_group=crash_second
    lessons.main()


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--worker':worker()
    else:unittest.main(verbosity=2)
