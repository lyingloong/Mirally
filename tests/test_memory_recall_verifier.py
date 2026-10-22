"""Offline acceptance-tool safety and evidence checks; no Gateway/API is started."""
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import yaml
from mirally.core.memory import MemoryStore
from mirally.runtime.activity.config import load_activity_config
from mirally.runtime.memory.config import load_memory_config
from tools import verify_memory_recall as verifier


class MemoryRecallVerifierTests(unittest.TestCase):
    def test_sqlite_snapshot_includes_committed_wal_and_never_mutates_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / 'live.sqlite3', root / 'copy.sqlite3'
            with closing(sqlite3.connect(source)) as writer:
                writer.execute('PRAGMA journal_mode=WAL')
                writer.execute('PRAGMA wal_autocheckpoint=0')
                writer.execute('CREATE TABLE evidence (value TEXT)')
                writer.execute("INSERT INTO evidence VALUES ('committed WAL evidence')")
                writer.commit()
                before = writer.execute('PRAGMA data_version').fetchone()
                verifier.snapshot_database(source, target)
                self.assertEqual(writer.execute('PRAGMA data_version').fetchone(), before)
                with closing(sqlite3.connect(target)) as reader:
                    self.assertEqual(reader.execute('SELECT value FROM evidence').fetchone()[0], 'committed WAL evidence')
                with self.assertRaises(ValueError):
                    verifier.snapshot_database(source, source)
                with self.assertRaises(ValueError):
                    verifier.snapshot_database(source, target)

    def test_questions_never_contain_expected_answer_and_pin_both_dates(self):
        questions = verifier.questions('2026-10-15', '2026-10-14')
        self.assertEqual([row['kind'] for row in questions], ['recall', 'missing', 'ordinary', 'natural', 'corpus'])
        self.assertIn('2026-10-14', questions[1]['prompt'])
        self.assertIn('北京时间', questions[0]['prompt'])
        self.assertNotIn('BV1fixture', json.dumps(questions))
        self.assertEqual(questions[3]['prompt'], '今天凌晨我看了一个有意思的视频，你记得是啥吗')
        self.assertIn('corpus="all"', questions[4]['prompt'])

    def test_prepare_clones_only_memory_and_disables_all_background_source_work(self):
        with tempfile.TemporaryDirectory(prefix='记忆 acceptance ') as temporary:
            root = Path(temporary)
            source_memory = root / 'production-memory.sqlite3'
            stamp = int(datetime(2026, 10, 14, 17, tzinfo=timezone.utc).timestamp() * 1000)
            with MemoryStore(source_memory) as store:
                store.put_memory({'id': 'm1', 'kind': 'episodic', 'title': 'fixture title',
                    'content': 'fixture title BV1fixture', 'source_observation_ids': ['obs1'],
                    'observed_start_utc_ms': stamp, 'observed_end_utc_ms': stamp + 1000})
            memory_profile = root / 'memory.yaml'
            memory_profile.write_text(yaml.safe_dump({'storage': {'database_path': str(source_memory)},
                'semantics': {'enabled': True}, 'raw_retention': {'enabled': True}}), encoding='utf-8')
            source_config = root / 'openclaw.json'
            original = {'models': {'providers': {'synthetic': {'apiKey': 'private fixture'}}},
                'agents': {'defaults': {'model': {'primary': 'synthetic/test'},
                    'workspace': 'DO NOT READ', 'memorySearch': {'extraPaths': ['DO NOT READ']}}},
                'plugins': {'entries': {'mirally-memory': {'config': {'root': str(root), 'configPath': str(memory_profile)}}}},
                'hooks': {'internal': {'enabled': True}}}
            source_config.write_text(json.dumps(original), encoding='utf-8')
            with patch.object(verifier, 'start_gateway', side_effect=AssertionError('must not start')):
                config, profile = verifier.prepare(source_config, root / 'isolated')
            cloned = json.loads(config.read_text(encoding='utf-8'))
            effective = load_memory_config(verifier.ROOT, profile)
            self.assertFalse(effective.semantics.enabled)
            self.assertEqual(effective.semantics.max_calls_per_hour, 0)
            self.assertEqual(effective.semantics.max_tokens_per_day, 0)
            self.assertFalse(effective.raw_retention.enabled)
            activity = load_activity_config(verifier.ROOT, effective.activity_config_path)
            self.assertFalse(activity.enabled)
            self.assertFalse(activity.database_path.exists())
            self.assertNotIn('memorySearch', cloned['agents']['defaults'])
            self.assertNotIn('hooks', cloned)
            self.assertEqual(cloned['models'], original['models'])
            self.assertEqual(json.loads(source_config.read_text()), original)
            records = verifier.expected_records(effective.database_path, 'fixture title', 'BV1fixture', '2026-10-15', '2026-10-14')
            self.assertEqual(records[0]['id'], 'm1')
            with self.assertRaises(ValueError):
                verifier.expected_records(effective.database_path, 'fixture title', 'BV1fixture', '2026-10-15', '2026-10-15')
            minimal_config, _ = verifier.prepare(source_config, root / 'minimal', minimal_workspace=True)
            minimal = json.loads(minimal_config.read_text(encoding='utf-8'))
            guidance = (Path(minimal['agents']['defaults']['workspace']) / 'AGENTS.md').read_text(encoding='utf-8')
            self.assertEqual(guidance, 'User timezone: Asia/Shanghai (UTC+08:00). This is a read-only session.\n')

    def test_receipt_and_corpus_need_actual_native_evidence(self):
        key = 'isolated-session'
        after = {'recallCounters': {'injected': 1}, 'lastRecall': {
            'sessionKeyHash': hashlib.sha256(key.encode()).hexdigest()[:24], 'memoryIds': ['m1']}}
        turn = {'kind': 'recall', 'session_key': key, 'before': {'recallCounters': {'injected': 0}},
            'after': after, 'final': '2026-10-15 fixture title BV1fixture 来源 mirally-memory/m1', 'calls': [], 'results': []}
        self.assertTrue(all(verifier.evaluate(turn, 'fixture title', 'BV1fixture', '2026-10-15', '2026-10-14', {'m1'}).values()))
        after['lastRecall']['memoryIds'] = ['wrong']
        self.assertFalse(verifier.evaluate(turn, 'fixture title', 'BV1fixture', '2026-10-15', '2026-10-14', {'m1'})['receipt_memory'])
        calls = [{'id': 's', 'name': 'memory_search', 'arguments': {'corpus': 'all', 'query': 'B站 买瓜'}},
            {'id': 'g', 'name': 'memory_get', 'arguments': {'path': 'mirally-memory/m1'}}]
        results = [{'toolCallId': 'g', 'content': [{'type': 'text', 'text': 'mirally-memory/m1 2026-10-15 fixture title BV1fixture'}]}]
        self.assertTrue(all(verifier.corpus_checks(calls, results, 'fixture title', 'BV1fixture', '2026-10-15').values()))
        wrapped = [{**call, 'name': 'tool_call', 'arguments': {'id': 'openclaw:memory-core:' + call['name'],
            'args': call['arguments']}} for call in calls]
        self.assertTrue(all(verifier.corpus_checks(wrapped, results, 'fixture title', 'BV1fixture', '2026-10-15').values()))
        self.assertTrue(verifier.has_beijing_date('2026-10-14T20:30:00Z', '2026-10-15'))
        self.assertFalse(verifier.has_beijing_date('2026-10-14T12:30:00Z', '2026-10-15'))
        results[0]['toolCallId'] = 'unrelated'
        self.assertFalse(verifier.corpus_checks(calls, results, 'fixture title', 'BV1fixture', '2026-10-15')['native_read_content'])

    def test_wrong_day_and_ordinary_contamination_fail(self):
        base = {'session_key': 's', 'before': {'recallCounters': {'injected': 1}},
            'after': {'recallCounters': {'injected': 1}}, 'calls': [], 'results': []}
        good = verifier.evaluate({**base, 'kind': 'missing', 'final': '没有昨天的记录，无法确认。'},
            'title', 'BVfixture', '2026-10-15', '2026-10-14', {'m1'})
        self.assertTrue(all(good.values()))
        bad = verifier.evaluate({**base, 'kind': 'missing', 'final': '昨天是 title BVfixture'},
            'title', 'BVfixture', '2026-10-15', '2026-10-14', {'m1'})
        self.assertFalse(bad['no_wrong_day_answer'])
        ordinary = verifier.evaluate({**base, 'kind': 'ordinary', 'final': '60 分钟',
            'after': {'recallCounters': {'injected': 2}}}, 'title', 'BVfixture', '2026-10-15', '2026-10-14', {'m1'})
        self.assertFalse(ordinary['no_injection'])

    def test_protocol_fixture_requires_evidence_and_performs_native_tool_sequence(self):
        title, bv, today = 'secret title', 'BVfixture', '2026-10-15'
        question = verifier.questions(today, '2026-10-14')[0]['prompt']
        no_evidence = verifier.fixture_reply({'messages': [{'role': 'user', 'content': question}]}, title, bv, today)
        self.assertNotIn(title, no_evidence['content'])
        injected = verifier.fixture_reply({'messages': [{'role': 'system', 'content': f'{today} {title} {bv} mirally-memory/m1'},
            {'role': 'user', 'content': question}]}, title, bv, today)
        self.assertIn(title, injected['content'])
        messages = [{'role': 'user', 'content': verifier.questions(today, '2026-10-14')[4]['prompt']}]
        body = {'messages': messages, 'tools': [{'function': {'name': 'memory_search'}}, {'function': {'name': 'memory_get'}}]}
        search = verifier.fixture_reply(body, title, bv, today)
        self.assertEqual(search['tool_calls'][0]['function']['name'], 'memory_search')
        messages.extend([search, {'role': 'tool', 'content': 'mirally-memory/m1'}])
        read = verifier.fixture_reply(body, title, bv, today)
        self.assertEqual(read['tool_calls'][0]['function']['name'], 'memory_get')
        messages.extend([read, {'role': 'tool', 'content': f'mirally-memory/m1 {today} {title} {bv}'}])
        self.assertIn(bv, verifier.fixture_reply(body, title, bv, today)['content'])

    def test_date_denial_and_wrong_beijing_day_do_not_pass_by_containing_today(self):
        wrong = '记录时间：北京时间 **2026-10-14 凌晨约04:30**，不是今天（2026-10-15）凌晨。'
        checks = verifier.answer_date_checks(wrong, '2026-10-15', '2026-10-14')
        self.assertFalse(checks['answer_date'])
        self.assertFalse(checks['no_wrong_beijing_date'])
        self.assertFalse(checks['not_denying_expected_today'])
        correct = '北京时间2026-10-15凌晨04:30；原始UTC日期为2026-10-14T20:30:00Z。'
        self.assertTrue(all(verifier.answer_date_checks(correct, '2026-10-15', '2026-10-14').values()))
        both = '北京时间2026-10-15（UTC2026-10-14T20:30:00Z）。'
        self.assertTrue(all(verifier.answer_date_checks(both, '2026-10-15', '2026-10-14').values()))

    def test_missing_day_allows_explicit_exclusion_of_today_but_rejects_real_wrong_attribution(self):
        base = {'kind': 'missing', 'session_key': 's', 'before': {'recallCounters': {'injected': 0}},
            'after': {'recallCounters': {'injected': 0}}, 'calls': [], 'results': []}
        correct = ('不知道，昨天（2026-10-14）没有可确认的记录。'
            '不带日期过滤能搜到带着豆包去买瓜 BV1w7hn63EBs，'
            '但观察时间是2026-10-15凌晨04:36，属于今天的记录，不能算作昨天。')
        wrong = ('找到了，昨天（北京时间2026-10-14）的记录里确实有一个B站视频：'
            '带着豆包去买瓜 BV1w7hn63EBs。记录时间：北京时间10-14凌晨04:30。')
        args = ('带着豆包去买瓜', 'BV1w7hn63EBs', '2026-10-15', '2026-10-14', {'m1'})
        self.assertTrue(all(verifier.evaluate({**base, 'final': correct}, *args).values()))
        scoped = ('不知道，昨天记录里查不到。我按北京时间2026-10-14全天做了限定检索。'
            '带着豆包去买瓜 BV1w7hn63EBs 的记录是今天2026-10-15凌晨，不是昨天，不能拿来当昨天。')
        self.assertTrue(all(verifier.evaluate({**base, 'final': scoped}, *args).values()))
        self.assertFalse(verifier.evaluate({**base, 'final': wrong}, *args)['no_wrong_day_answer'])

    def test_natural_case_requires_actual_dawn_evidence_not_just_expected_title(self):
        key = 'agent:main:dashboard:test'
        candidate = {'memory_id': 'real-dawn', 'titles': ['Another actual dawn video'],
            'bv_ids': ['BVreal'], 'observed_start_local': '2026-10-15T04:30:00+08:00'}
        turn = {'session_key': key, 'before': {'recallCounters': {'injected': 0}},
            'after': {'recallCounters': {'injected': 1}, 'lastRecall': {'memoryIds': ['real-dawn'],
            'sessionKeyHash': hashlib.sha256(key.encode()).hexdigest()[:24]}}, 'results': [],
            'final': '今天凌晨的候选有 Another actual dawn video，记录为2026-10-15凌晨04:30。'}
        self.assertTrue(all(verifier.evaluate_natural(turn, [candidate], '2026-10-15', '2026-10-14').values()))
        turn['after'] = {}
        self.assertFalse(verifier.evaluate_natural(turn, [candidate], '2026-10-15', '2026-10-14')['candidate_backed_by_actual_retrieval'])
        turn['final'] = '凌晨没有记录，如果是下午的马斯克视频那可能是它。'
        self.assertFalse(verifier.evaluate_natural(turn, [candidate], '2026-10-15', '2026-10-14')['identifies_real_dawn_candidate'])

    def test_dawn_candidates_exclude_afternoon_and_require_actual_video_url(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / 'memory.sqlite3'
            base = {'kind': 'episodic', 'source_observation_ids': ['obs1']}
            with MemoryStore(database) as store:
                for identifier, hour, content in [('dawn', 20, 'Window title: 哔哩哔哩 (゜-゜)つロ 干杯~-bilibili\nWindow title: YouTube - Home\nWindow title: Dawn video_哔哩哔哩\nURL: https://www.bilibili.com/video/BVdawn/'),
                    ('not-video', 20, 'Window title: Random workspace'),
                    ('afternoon', 8, 'Window title: Afternoon video_哔哩哔哩\nURL: https://www.bilibili.com/video/BVafternoon/')]:
                    day = 14 if hour == 20 else 15
                    stamp = int(datetime(2026, 10, day, hour, 30, tzinfo=timezone.utc).timestamp() * 1000)
                    store.put_memory({**base, 'id': identifier, 'content': content,
                        'observed_start_utc_ms': stamp, 'observed_end_utc_ms': stamp + 1000})
            rows = verifier.natural_candidates(database, '2026-10-15')
            self.assertEqual([row['memory_id'] for row in rows], ['dawn'])
            self.assertEqual(rows[0]['titles'], ['Dawn video'])


if __name__ == '__main__':
    unittest.main()
