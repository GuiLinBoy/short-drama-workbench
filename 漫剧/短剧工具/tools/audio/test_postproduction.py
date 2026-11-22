"""Batch 2 correctness regressions, entirely local."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import align
import assemble


class SubtitleTests(unittest.TestCase):
    def test_script_comma_does_not_shift_next_sentence(self):
        res = {'text': '你好，世界。再见。', 'timestamp': [[i*300, i*300+250] for i in range(6)]}
        cues = align.align_script(res, '你好，世界。再见。')
        self.assertEqual([x['text'] for x in cues], ['你好，世界。', '再见。'])
        self.assertEqual(cues[1]['start'], 1.2)
        self.assertEqual(cues[0]['end'], 1.15)

    def test_missing_recognition_is_refused(self):
        res = {'text': '你好世界', 'timestamp': [[i*300, i*300+250] for i in range(4)]}
        with self.assertRaises(SystemExit):
            align.align_script(res, '你好世界，再见。')

    def test_english_word_timestamps(self):
        res = {'text': 'Hello, world. Goodbye.', 'timestamp': [[100,500],[600,1000],[1400,1900]]}
        cues = align.align_script(res, 'Hello, world.\nGoodbye.')
        self.assertEqual([(x['start'],x['end']) for x in cues], [(.1,1.0),(1.4,1.9)])

    def test_spaces_do_not_duplicate_timestamp_consumption(self):
        result = align.sentences_from_timestamp([[0,100],[100,200],[200,300],[300,400]], '你 好 世界。')
        self.assertEqual(''.join(x['text'] for x in result), '你好世界')

    def test_timestamp_mismatch_is_refused(self):
        with self.assertRaises(SystemExit):
            align.sentences_from_timestamp([[0,100]], '你好世界')


class RenderContracts(unittest.TestCase):
    def test_unknown_ratio_is_refused(self):
        with self.assertRaises(SystemExit):
            assemble.output_dimensions('bad-ratio')

    def test_visible_label_does_not_turn_on_subtitles(self):
        self.assertFalse(assemble.should_burn({'mode':'series','post':{'burn_subtitles':False,
                         'aigc_label':{'visible':True}}}, None))
        self.assertTrue(assemble.should_burn({'mode':'short'}, None))

    def test_malformed_srt_is_not_silently_dropped(self):
        with self.assertRaises(SystemExit):
            assemble.parse_srt('1\n00:00:xx --> invalid\n字幕')

    def test_out_of_bounds_subtitle_is_refused(self):
        with self.assertRaises(SystemExit):
            assemble.validate_cues([{'start':3,'end':5,'text':'越界'}],4)

    def test_overlapping_duck_windows_do_not_stack(self):
        self.assertEqual(assemble.merge_windows([(1,3),(2,4),(5,6)]),[(1,4),(5,6)])

    def test_publish_requires_both_labels(self):
        with self.assertRaises(SystemExit):
            assemble.check_publication({'publish':True,'post':{'aigc_label':{'visible':False,'metadata':True}}})

    def test_music_registry_matches_full_path_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as d:
            global_root=Path(d)/'漫剧'
            root=global_root/'作品'/'A'
            root.mkdir(parents=True)
            library=global_root/'曲库'
            (library/'a').mkdir(parents=True)
            (library/'b').mkdir()
            (library/'a/song.wav').write_bytes(b'first synthetic file')
            (library/'b/song.wav').write_bytes(b'second synthetic file')
            (library/'proof.txt').write_text('SIMULATED authorization fixture',encoding='utf-8')
            track={'file':'b/song.wav','license_verified':True,'license':'SIMULATED',
                   'source_url':'https://example.invalid/license','attribution':'SIMULATED attribution',
                   'license_evidence':'proof.txt','sha256':assemble.digest(library/'b/song.wav')}
            (library/'曲库.json').write_text(json.dumps({'tracks':[track]}),encoding='utf-8')
            with self.assertRaises(SystemExit):
                assemble.music_for(SimpleNamespace(music='a/song.wav'),root,publish=True)
            _,info=assemble.music_for(SimpleNamespace(music='b/song.wav'),root,publish=True)
            self.assertTrue(info['verified'])
            (library/'b/song.wav').write_bytes(b'changed recording')
            with self.assertRaises(SystemExit):
                assemble.music_for(SimpleNamespace(music='b/song.wav'),root,publish=True)

    def test_caption_overflow_is_refused_without_truncation(self):
        with self.assertRaises(SystemExit):
            assemble.render_srt([{'start':0,'end':2,'text':'很长的字幕'*50}])


if __name__ == '__main__':
    unittest.main(verbosity=2)
