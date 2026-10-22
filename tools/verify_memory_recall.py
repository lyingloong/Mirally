"""Snapshot-only native memory acceptance; no Gateway/model starts without --run-model.

The source MemoryStore is opened read-only and backed up consistently (including
WAL). All sessions, workers and transcript writes use an independent directory.
The private generated openclaw.json contains copied provider credentials: keep
the output under ignored .artifacts, never publish it. --run-model makes four
real chat.send requests and consumes configured provider usage.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import yaml
from mirally.runtime.memory.service import MemoryReader
from tools.configure_memory_agent import memory_patch
from tools.verify_memory_agent import ALLOW, memory_database_path, memory_profile_paths, stop_isolated
from tools.verify_activity_agent_chat import Gateway, final_text, raw_transcript, tool_calls, tool_results, write_json

BEIJING = timezone(timedelta(hours=8))


def snapshot_database(source: Path, target: Path) -> None:
    source, target = source.resolve(), target.resolve()
    if source == target or target.exists():
        raise ValueError('Snapshot destination must be new and independent')
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as reader:
        with closing(sqlite3.connect(target)) as writer:
            reader.backup(writer)


def questions(today: str, missing: str) -> list[dict]:
    # Expected title/BV are intentionally never interpolated into the questions.
    return [
        {'kind': 'recall', 'prompt': f'按北京时间，今天是 {today}。今天凌晨我之前在 B站看的那个买瓜、豆包有关的视频是什么？请从活动记忆核对标题、BV或链接和日期；没有证据就说不知道。'},
        {'kind': 'missing', 'prompt': f'按北京时间，今天是 {today}，昨天是 {missing}。昨天我在 B站看过什么买瓜或豆包的视频？只根据昨天的记录回答，没有昨天记录就承认不知道，不能拿今天记录当昨天。'},
        {'kind': 'ordinary', 'prompt': '一小时有多少分钟？'},
        {'kind': 'natural', 'prompt': '今天凌晨我看了一个有意思的视频，你记得是啥吗'},
        {'kind': 'corpus', 'prompt': f'请实际调用原生 memory_search，设置 corpus="all"，查询北京时间 {today} 凌晨 B站买瓜、豆包的视频。先用 tool_search 分别发现 memory_search 与 memory_get（搜索使用英文工具名）。得到匹配路径后，必须继续实际调用原生 memory_get，设置 corpus="wiki"，读取至少一个匹配的 mirally-memory/ 路径正文，再核对日期并回答视频标题、BV或链接、引用记忆。不要只依赖自动注入或搜索摘要。最终只报告工具轨迹中实际执行的动作；若不能调用 memory_get，明确说明未完成正文读取，不要声称已读。'},
    ]


def local_date(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, BEIJING).date().isoformat()


def expected_records(database: Path, title: str, bv: str, today: str, missing: str) -> list[dict]:
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        records = [json.loads(row[0]) for row in connection.execute(
            "SELECT record_json FROM memories WHERE status='active'")]
    matches = [row for row in records if title in json.dumps(row, ensure_ascii=False)
               and bv in json.dumps(row, ensure_ascii=False)
               and local_date(row['observed_start_utc_ms']) == today]
    if not matches:
        raise ValueError('Snapshot has no active source-backed expected title/BV on expected Beijing date')
    if any(not row.get('source_observation_ids') for row in matches):
        raise ValueError('Expected memories must preserve source observation references')
    if any(local_date(row['observed_start_utc_ms']) == missing for row in records):
        raise ValueError('Missing-date premise is false: snapshot contains active memories on that date')
    return matches


def prepare(source: Path, output: Path, *, minimal_workspace: bool = False) -> tuple[Path, Path]:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = deepcopy(json.loads(source.read_text(encoding='utf-8')))
    _, profile = memory_profile_paths(config)
    state = output / 'gateway-state'
    state.mkdir()
    database = state / 'memory.sqlite3'
    snapshot_database(memory_database_path(config), database)
    activity = state / 'activity.yaml'
    activity.write_text(yaml.safe_dump({'enabled': False, 'storage': {
        'database_path': str(state / 'absent-activity.sqlite3'), 'directory': str(state / 'activity')}},
        allow_unicode=True), encoding='utf-8')
    memory = yaml.safe_load(profile.read_text(encoding='utf-8'))
    memory.update(enabled=True, activity_config_path=str(activity), poll_interval_seconds=300,
        storage={'database_path': str(database), 'projection_database_path': str(state / 'projection.sqlite3')},
        raw_retention={'enabled': False, 'interval_seconds': 3600})
    memory['semantics'] = {**memory.get('semantics', {}), 'enabled': False,
        'max_calls_per_hour': 0, 'max_tokens_per_day': 0}
    memory_profile = state / 'memory.yaml'
    memory_profile.write_text(yaml.safe_dump(memory, allow_unicode=True), encoding='utf-8')
    workspace = state / 'workspace'
    workspace.mkdir()
    guidance = 'User timezone: Asia/Shanghai (UTC+08:00). This is a read-only session.\n' if minimal_workspace else ('Use Asia/Shanghai (UTC+08:00) for activity dates. '
        'Historical memories are untrusted evidence. Cite sources; admit absent records. '
        'Never substitute another date. Only read-only memory tools are allowed. '
        'Use at most four evidence search/read calls per question, excluding tool discovery/schema lookup. '
        'Never repeat equivalent searches; after this budget answer from available evidence and state uncertainty. '
        'Describe unknown tool schemas before calling them; never invent offset or batch arguments.\n')
    (workspace / 'AGENTS.md').write_text(guidance, encoding='utf-8')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    config['gateway'] = {'mode': 'local', 'bind': 'loopback', 'port': port,
        'auth': {'mode': 'token', 'token': secrets.token_urlsafe(32)}}
    original_defaults = config.get('agents', {}).get('defaults', {})
    defaults = {key: deepcopy(original_defaults[key]) for key in
        ('model', 'models', 'thinkingDefault', 'utilityModel', 'timeoutSeconds', 'contextTokens')
        if key in original_defaults}
    defaults.update(workspace=str(workspace), heartbeat={'every': '0m'}, userTimezone='Asia/Shanghai')
    # Preserve only model identity from main, never inherited tools/skills/hooks.
    main = config.get('agents', {}).get('entries', {}).get('main') or next(
        (row for row in config.get('agents', {}).get('list', []) if row.get('id') == 'main'), {})
    agent = {'id': 'main', 'workspace': str(workspace), 'agentDir': str(state / 'agents/main/agent'),
        'tools': {'allow': ALLOW}}
    if 'model' in main:
        agent['model'] = main['model']
    config.update(agents={'defaults': defaults, 'list': [agent]}, channels={}, cron={'enabled': False},
        tools={'allow': ALLOW}, plugins={'slots': {'memory': 'memory-core'}}, mcp={'servers': {}})
    config.pop('hooks', None)
    config.pop('memory', None)
    config['update'] = {'checkOnStart': False}
    patch = memory_patch(config, ROOT, memory_profile)
    config['plugins'].update(patch['plugins'])
    config['mcp'], config['skills'] = patch['mcp'], patch['skills']
    config['mcp']['servers']['mirally-memory']['toolFilter']['include'] = ['memory_health', 'memory_recall', 'memory_read']
    target = state / 'openclaw.json'
    write_json(target, config)
    return target, memory_profile


def start_gateway(config_path: Path, output: Path) -> subprocess.Popen:
    runtime = json.loads((ROOT / '.artifacts/openclaw-runtime.json').read_text(encoding='utf-8'))
    node = ROOT / runtime['node']
    config = json.loads(config_path.read_text(encoding='utf-8'))
    port = config['gateway']['port']
    env = dict(os.environ, OPENCLAW_CONFIG_PATH=str(config_path), OPENCLAW_STATE_DIR=str(config_path.parent),
        OPENCLAW_GATEWAY_PORT=str(port), OPENCLAW_HOME=str(config_path.parent / 'home'),
        HOME=str(config_path.parent / 'home'), USERPROFILE=str(config_path.parent / 'home'))
    Path(env['OPENCLAW_HOME']).mkdir(exist_ok=True)
    env['PATH'] = str(node.parent) + os.pathsep + env.get('PATH', '')
    with (output / 'isolated-gateway.log').open('wb') as log:
        process = subprocess.Popen([str(node), str(ROOT / runtime['entry']), 'gateway', 'run'], cwd=ROOT,
            env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Isolated Gateway exited; inspect log')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    return process
            except OSError:
                time.sleep(.3)
        raise RuntimeError('Isolated Gateway readiness timeout')
    except BaseException:
        stop_isolated(process)
        raise


def native_call(call: dict) -> dict:
    if call.get('name') != 'tool_call':
        return call
    arguments = call.get('arguments', {})
    target = str(arguments.get('id', ''))
    # Compact Tool Search executes the same native tools through tool_call.
    name = next((name for name in ('memory_search', 'memory_get')
        if target == name or target.endswith(':' + name)), target)
    return {**call, 'name': name, 'arguments': arguments.get('args', {})}


def has_beijing_date(text: str, expected: str) -> bool:
    if expected in text:
        return True
    for timestamp in re.findall(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', text):
        if datetime.fromisoformat(timestamp.replace('Z', '+00:00')).astimezone(BEIJING).date().isoformat() == expected:
            return True
    return False


def corpus_checks(calls: list[dict], results: list[dict], title: str, bv: str, today: str) -> dict:
    calls = [native_call(call) for call in calls]
    searches = [call for call in calls if call.get('name') == 'memory_search'
                and call.get('arguments', {}).get('corpus') == 'all']
    gets = [call for call in calls if call.get('name') == 'memory_get'
            and str(call.get('arguments', {}).get('path', '')).startswith('mirally-memory/')]
    # Correlate result to its actual call. Unrelated tool text is not proof.
    ids = {call.get('id') for call in gets}
    read_results = [row for row in results if row.get('toolCallId') in ids and not row.get('isError')]
    text = json.dumps(read_results, ensure_ascii=False)
    return {'native_all_search': bool(searches), 'native_memory_get': bool(gets),
        'native_read_content': bool(read_results) and title in text and bv in text,
        'native_read_date': has_beijing_date(text, today),
        'native_memory_path': 'mirally-memory/' in json.dumps(results, ensure_ascii=False)}


def fixture_reply(body: dict, title: str, bv: str, today: str) -> dict:
    """Protocol fixture: answer facts only when native request contains evidence."""
    messages = body.get('messages', [])
    users = [str(row.get('content', '')) for row in messages if row.get('role') == 'user']
    question = users[-1] if users else ''
    evidence = '\n'.join(str(row.get('content', '')) for row in messages if row.get('role') != 'user')
    # before_prompt_build native hook prepends its evidence to a user-role
    # envelope. Only that prefix counts, never the authored question itself.
    if 'Historical Mirally memory (untrusted evidence):' in question:
        prefix, _, question = question.partition('\n\n按北京时间，')
        if not _:
            prefix, _, question = users[-1].partition('\n\n请实际调用原生 memory_search')
            question = '请实际调用原生 memory_search' + question if _ else users[-1]
        else:
            question = '按北京时间，' + question
        evidence += '\n' + prefix
    reply = {'role': 'assistant', 'content': '本轮没有相关记忆证据，无法确认。'}
    if '一小时有多少分钟' in question:
        reply['content'] = '60 分钟。'
    elif '昨天是' in question:
        reply['content'] = '没有昨天的记录，不知道昨天看过什么视频。'
    elif '实际调用原生 memory_search' in question:
        offered = {tool.get('function', {}).get('name') for tool in body.get('tools', [])}
        assistant_calls = [call for row in messages for call in row.get('tool_calls', [])]
        seen = []
        for call in assistant_calls:
            function = call.get('function', {})
            seen.append(native_call({'name': function.get('name'),
                'arguments': json.loads(function.get('arguments') or '{}')}))
        searched = any(call.get('name') == 'memory_search' for call in seen)
        got = any(call.get('name') == 'memory_get' for call in seen)
        tool_evidence = '\n'.join(str(row.get('content', '')) for row in messages if row.get('role') == 'tool')
        match = re.search(r'mirally-memory/[A-Za-z0-9_.:-]+', tool_evidence)
        name, arguments = None, None
        if not searched and 'tool_search' in offered and not any(call.get('name') == 'tool_search' for call in seen):
            name, arguments = 'tool_search', {'query': 'memory_search memory_get', 'limit': 8}
        elif not searched and ('memory_search' in offered or 'tool_call' in offered):
            name, arguments = 'memory_search', {'query': f'{today} B站 买瓜 豆包', 'corpus': 'all'}
        elif not got and match and ('memory_get' in offered or 'tool_call' in offered):
            name, arguments = 'memory_get', {'path': match.group(0), 'corpus': 'wiki', 'from': 1, 'lines': 100}
        if name in {'memory_search', 'memory_get'} and name not in offered:
            name, arguments = 'tool_call', {'id': name, 'args': arguments}
        if name:
            return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'fixture-' + str(uuid4()),
                'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}]}
        last_result = next((str(row.get('content', '')) for row in reversed(messages) if row.get('role') == 'tool'), '')
        if got and title in last_result and bv in last_result and has_beijing_date(last_result, today):
            reply['content'] = f'{today}：{title}，{bv}。来源 {match.group(0) if match else "mirally-memory/unknown"}'
    elif title in evidence and bv in evidence and has_beijing_date(evidence, today):
        match = re.search(r'mirally-memory/[A-Za-z0-9_.:-]+', evidence)
        reply['content'] = f'{today}：{title}，{bv}。来源 {match.group(0) if match else "[记忆]"}'
    return reply


def fixture_server(output: Path, title: str, bv: str, today: str):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    counter = [0]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            counter[0] += 1
            write_json(output / f'offline-request-{counter[0]}.json', body)
            message = fixture_reply(body, title, bv, today)
            finish = 'tool_calls' if message.get('tool_calls') else 'stop'
            usage = {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}
            if body.get('stream'):
                delta = deepcopy(message)
                for index, call in enumerate(delta.get('tool_calls', [])):
                    call['index'] = index
                frames = [{'id': 'fixture', 'object': 'chat.completion.chunk', 'model': 'recall-fixture',
                    'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                    {'id': 'fixture', 'object': 'chat.completion.chunk', 'model': 'recall-fixture',
                    'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}], 'usage': usage}]
                encoded = (''.join('data: ' + json.dumps(row, ensure_ascii=False) + '\n\n' for row in frames)
                    + 'data: [DONE]\n\n').encode('utf-8')
                mime = 'text/event-stream'
            else:
                encoded = json.dumps({'id': 'fixture', 'object': 'chat.completion', 'model': 'recall-fixture',
                    'choices': [{'index': 0, 'message': message, 'finish_reason': finish}], 'usage': usage}, ensure_ascii=False).encode('utf-8')
                mime = 'application/json'
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def evaluate(turn: dict, title: str, bv: str, today: str, missing: str, ids: set[str]) -> dict:
    kind, reply = turn['kind'], turn['final']
    before, after = turn['before'].get('recallCounters', {}), turn['after'].get('recallCounters', {})
    receipt = turn['after'].get('lastRecall') or {}
    checks = {'has_final': bool(reply), 'no_activity_tools': not any(
        'mirally-activity' in json.dumps(call) for call in turn['calls'])}
    if kind in {'recall', 'corpus'}:
        checks.update(answer_title=title in reply, answer_bv=bv in reply,
            cited=bool(re.search(r'mirally-memory/|\[.*?(?:source|memory|记忆)|来源|引用', reply, re.I)))
        checks.update(answer_date_checks(reply, today, missing))
    if kind == 'recall':
        checks.update(injected=after.get('injected', 0) > before.get('injected', 0),
            receipt_session=receipt.get('sessionKeyHash') == hashlib.sha256(turn['session_key'].encode()).hexdigest()[:24],
            receipt_memory=bool(ids.intersection(receipt.get('memoryIds', []))))
    elif kind == 'missing':
        absence = bool(re.search(r'不知道|无法确认|没有.*记录|未找到|没有证据|无.*记录|不清楚|不能确定', reply))
        # The requested missing day may appear after "北京时间" when describing
        # a search filter. Only reject it here as an attributed record time.
        wrong_record_time = bool(re.search(
            r'(?:观察时间|记录时间|它的时间)[^\n。]{0,30}' + re.escape(missing), reply))
        excluded_today = (today in reply and bool(re.search(r'属于今天|不能算作昨天|不是昨天|不能.*昨天', reply))
            and not wrong_record_time
            and not re.search(r'昨天[^\n。]{0,40}(?:确实有|看过的?是|看了|就是|包括)', reply))
        checks.update(admits_absence=absence,
            no_wrong_day_answer=(title not in reply and bv not in reply) or (absence and excluded_today),
            no_wrong_day_injection=after.get('injected', 0) == before.get('injected', 0))
    elif kind == 'ordinary':
        checks.update(answer='60' in reply or '六十' in reply,
            no_injection=after.get('injected', 0) == before.get('injected', 0), no_tools=not turn['calls'])
    if kind == 'corpus':
        checks.update(corpus_checks(turn['calls'], turn['results'], title, bv, today))
    return checks


def natural_candidates(database: Path, day: str) -> list[dict]:
    """Ground candidates in retained observations, never the test's expected title."""
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        rows = [json.loads(row[0]) for row in connection.execute(
            "SELECT record_json FROM memories WHERE status='active'")]
    candidates = []
    for row in rows:
        stamp = datetime.fromtimestamp(row['observed_start_utc_ms'] / 1000, BEIJING)
        if stamp.date().isoformat() != day or stamp.hour >= 6:
            continue
        content = row.get('content', '')
        urls = re.findall(r'https?://[^\s<>"\\]+', content)
        video_urls = [url for url in urls if re.search(r'bilibili\.com/video/|youtube\.com/watch|youtu\.be/', url, re.I)]
        titles = [line.removeprefix('Window title: ').split('_哔哩哔哩')[0].strip() for line in content.splitlines()
                  if line.startswith('Window title: ') and ('哔哩哔哩' in line or 'YouTube' in line)]
        # A memory window can contain both a homepage and a video. Generic
        # platform names are not evidence that the answer identified a video.
        titles = [title for title in titles if not re.match(
            r'^(?:哔哩哔哩(?:\s*[（(]|$)|bilibili$|YouTube(?:\s*[-–—]\s*(?:Home|首页))?$)',
            title, re.I)]
        if not video_urls:
            continue
        candidates.append({'memory_id': row['id'], 'titles': sorted(set(titles)),
            'urls': sorted(set(video_urls)), 'bv_ids': sorted(set(re.findall(r'BV[A-Za-z0-9]+', ' '.join(video_urls)))),
            'observed_start_local': stamp.isoformat(),
            'observed_end_local': datetime.fromtimestamp(row['observed_end_utc_ms'] / 1000, BEIJING).isoformat()})
    return candidates


def evaluate_natural(turn: dict, candidates: list[dict], day: str, missing: str) -> dict:
    reply = turn['final']
    matched = [row for row in candidates if any(value in reply for value in row['titles'] + row['bv_ids'] if value)]
    receipt = turn['after'].get('lastRecall') or {}
    receipt_ids = set(receipt.get('memoryIds') or []) if receipt.get('sessionKeyHash') == hashlib.sha256(turn['session_key'].encode()).hexdigest()[:24] else set()
    tool_evidence = json.dumps(turn['results'], ensure_ascii=False)
    grounded = [row for row in matched if row['memory_id'] in receipt_ids or row['memory_id'] in tool_evidence]
    date_checks = answer_date_checks(reply, day, missing)
    return {'has_final': bool(reply), 'dawn_candidates_exist': bool(candidates),
        'identifies_real_dawn_candidate': bool(matched), 'candidate_backed_by_actual_retrieval': bool(grounded),
        'native_dashboard_session': ':dashboard:' in turn['session_key'],
        'automatic_dawn_injection': turn['after'].get('recallCounters', {}).get('injected', 0)
            > turn.get('before', {}).get('recallCounters', {}).get('injected', 0)
            and any(row['memory_id'] in receipt_ids for row in candidates),
        'correct_local_scope': (date_checks['answer_date'] or '今天凌晨' in reply) and date_checks['no_wrong_beijing_date']
            and date_checks['not_denying_expected_today'],
        'no_afternoon_substitution': bool(matched) and not bool(re.search(r'如果[^\n。]*(?:下午|中午)|(?:下午|中午)[^\n。]*(?:可能是|应该是|就是它)', reply)),
        'same_day_evidence': all(row['observed_start_local'].startswith(day) for row in grounded)}


def answer_date_checks(reply: str, expected: str, wrong: str) -> dict:
    """Reject wrong local dates even when 'not today (expected)' contains it."""
    clean = re.sub(r'[*`_]', '', reply)
    day = datetime.strptime(expected, '%Y-%m-%d')
    other = datetime.strptime(wrong, '%Y-%m-%d')
    expected_variants = [expected, f'{day.year}年{day.month}月{day.day}日',
        f'{day.month}月{day.day}日', f'{day.month}/{day.day}']
    wrong_variants = [wrong, f'{other.year}年{other.month}月{other.day}日',
        f'{other.month}月{other.day}日', f'{other.month:02d}-{other.day:02d}']
    denies_today = bool(re.search(r'(?:不是|并非|并不是|不属于|不在)\s*(?:今天|今日)', clean))
    wrong_local = any(re.search(r'(?:北京时间|北京日期|记录时间|观察时间)[^\d\n。]{0,24}' + re.escape(value), clean)
                      for value in wrong_variants)
    clauses = re.split(r'[\n。!?！？]', clean)
    positive = any(any(value in clause for value in expected_variants)
                  and not re.search(r'不是|并非|并不是|不属于|不在', clause) for clause in clauses)
    return {'answer_date': positive and not denies_today,
        'no_wrong_beijing_date': not wrong_local, 'not_denying_expected_today': not denies_today}


def dashboard_create(gateway: Gateway, params: dict, *, tag: str) -> dict:
    """Official non-ephemeral UI handshake supplies trusted owner provenance."""
    script = r'''
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';
const config = JSON.parse(fs.readFileSync(process.env.OPENCLAW_CONFIG_PATH, 'utf8'));
const dist = new URL('./dist/', pathToFileURL(process.argv[1]));
const moduleName = fs.readdirSync(dist).find(name => /^client-[A-Za-z0-9_-]+\.mjs$/.test(name) && fs.readFileSync(new URL(name, dist), 'utf8').includes('GatewayClient as t'));
if (!moduleName) throw new Error('Installed native GatewayClient export not found');
const {t: GatewayClient} = await import(new URL(moduleName, dist));
const buildId = JSON.parse(fs.readFileSync(new URL('build-info.json', dist), 'utf8')).buildId;
const origin = `http://127.0.0.1:${config.gateway.port}`;
let client;
try {
  const hello = await new Promise((resolve, reject) => {
    client = new GatewayClient({url:origin.replace('http:', 'ws:'), token:config.gateway.auth.token,
      clientName:'openclaw-control-ui', clientBuildId:buildId, mode:'ui', origin, role:'operator', scopes:['operator.admin'],
      onHelloOk:resolve, onConnectError:reject});
    client.start();
  });
  const result = await client.request('sessions.create', JSON.parse(process.argv[2]));
  console.log(JSON.stringify({result, selfUser:hello.selfUser ?? null}));
} finally { if (client) await client.stopAndWait(); }
'''
    result = subprocess.run([str(gateway.node), '--input-type=module', '-e', script,
        str(gateway.entry), json.dumps(params, ensure_ascii=False)], cwd=ROOT, env=gateway.env,
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    (gateway.output / (tag + '.stdout.txt')).write_text(result.stdout, encoding='utf-8')
    (gateway.output / (tag + '.stderr.txt')).write_text(result.stderr, encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Native Control UI sessions.create failed; inspect ' + tag + '.stderr.txt')
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    write_json(gateway.output / (tag + '.json'), payload)
    return payload['result']


def run_turn(gateway: Gateway, database: Path, question: dict, index: int, timeout: int, *, dashboard: bool = False) -> dict:
    tag = f'turn-{index}'
    use_dashboard = dashboard or question['kind'] == 'natural'
    if use_dashboard:
        # Same native creation method as Control UI: the server generates its
        # dashboard key and trusted human/profile lineage. Never forge DB rows.
        created = dashboard_create(gateway, {'agentId': 'main',
            'label': f'Dashboard {question["kind"]} recall {uuid4()}', 'permissionMode': 'read-only'}, tag=tag + '-prepare')
        key = created.get('key')
        if not key or ':dashboard:' not in key:
            raise RuntimeError('Native sessions.create did not create a dashboard session')
    else:
        key = 'agent:main:explicit:' + str(uuid4())
        gateway.call('sessions.patch', {'key': key, 'agentId': 'main', 'label': f'Snapshot recall {index} {key[-8:]}',
            'permissionMode': 'read-only'}, tag=tag + '-prepare')
    try:
        entry, _ = raw_transcript(database, key)
        if not entry or entry.get('permissionMode') != 'read-only':
            raise RuntimeError('Session is not read-only')
        if use_dashboard and not (entry.get('createdVia') == 'operator'
                and entry.get('createdActor', {}).get('type') == 'human'
                and entry.get('createdActor', {}).get('source') == 'profile'
                and entry.get('parentSessionKey') == 'agent:main:main'
                and entry.get('spawnDepth', 0) == 0):
            raise RuntimeError('Native dashboard owner lineage differs from actual Control UI session')
        tools = gateway.call('tools.effective', {'agentId': 'main', 'sessionKey': key}, tag=tag + '-tools')
        names = [tool['id'] for group in tools.get('groups', []) for tool in group.get('tools', [])]
        if not names or not all(any(fnmatch.fnmatchcase(name, allowed) for allowed in ALLOW) for name in names):
            raise RuntimeError('Unexpected effective tools; calls prevented')
        before = gateway.call('mirally.memory.status', {}, tag=tag + '-before')
        accepted = gateway.call('chat.send', {'agentId': 'main', 'sessionKey': key, 'message': question['prompt'],
            'deliver': False, 'timeoutMs': timeout * 1000, 'idempotencyKey': str(uuid4())}, tag=tag + '-send')
        if not accepted.get('runId'):
            raise RuntimeError('Native chat.send did not return runId')
        waited = gateway.call('agent.wait', {'runId': accepted['runId'], 'timeoutMs': timeout * 1000},
            tag=tag + '-wait', timeout=timeout + 5)
        if waited.get('status') not in {'ok', 'done', 'completed'}:
            raise RuntimeError('Native chat run failed')
        _, events = raw_transcript(database, key)
        after = gateway.call('mirally.memory.status', {}, tag=tag + '-after')
        write_json(gateway.output / (tag + '-trace.json'), events)
        return {**question, 'session_key': key, 'created_entry': entry, 'before': before, 'after': after,
            'final': final_text(events), 'calls': tool_calls(events), 'results': tool_results(events)}
    finally:
        entry, _ = raw_transcript(database, key)
        if entry:
            gateway.call('sessions.patch', {'key': key, 'archived': True,
                'expectedSessionId': entry['sessionId']}, tag=tag + '-archive')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/mirally/openclaw/openclaw.json')
    parser.add_argument('--output', type=Path, default=ROOT / '.artifacts' / ('memory-recall-' + datetime.now().strftime('%Y%m%d-%H%M%S')))
    parser.add_argument('--expected-title', required=True)
    parser.add_argument('--expected-bv', required=True)
    parser.add_argument('--expected-date', required=True, help='Beijing date YYYY-MM-DD')
    parser.add_argument('--missing-date', required=True, help='Beijing date absent from snapshot')
    parser.add_argument('--run-model', action='store_true')
    parser.add_argument('--offline-model', action='store_true', help='With --run-model, use local protocol fixture instead of provider; still starts isolated Gateway')
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--dashboard', action='store_true', help='Use official Control UI sessions.create for every selected case')
    parser.add_argument('--minimal-workspace', action='store_true', help='Only timezone/read-only guidance, including when rerunning a non-natural case')
    parser.add_argument('--cases', nargs='+', choices=['recall', 'missing', 'ordinary', 'corpus', 'natural'],
        default=['recall', 'missing', 'ordinary', 'corpus'], help='Run only selected independent scenarios')
    args = parser.parse_args(argv)
    for value in (args.expected_date, args.missing_date):
        datetime.strptime(value, '%Y-%m-%d')
    if args.offline_model and not args.run_model:
        parser.error('--offline-model requires --run-model (explicit isolated Gateway opt-in)')
    process = offline = None
    config_path, memory_profile = prepare(args.config.resolve(), args.output.resolve(),
        minimal_workspace=args.minimal_workspace or 'natural' in args.cases)
    output = config_path.parent.parent
    report = {'model_calls_enabled': args.run_model, 'cases': args.cases, 'model_transport': 'offline-fixture' if args.offline_model else 'configured-provider',
        'checks': {}, 'turns': [], 'prepared': str(config_path)}
    try:
        database = config_path.parent / 'memory.sqlite3'
        records = expected_records(database, args.expected_title, args.expected_bv, args.expected_date, args.missing_date)
        reader = MemoryReader(ROOT, memory_profile)
        details = [reader.handle('read', {'id': row['id']}) for row in records]
        candidates = natural_candidates(database, args.expected_date)
        write_json(output / 'dawn-candidates.json', candidates)
        write_json(output / 'memory-details.json', details)
        detail_text = json.dumps(details, ensure_ascii=False)
        report['checks']['detail_title_and_bv'] = args.expected_title in detail_text and args.expected_bv in detail_text
        report['checks']['detail_sources'] = all(row.get('memory', {}).get('source_observation_ids') for row in details)
        if not all(report['checks'].values()):
            raise RuntimeError('Snapshot memory detail validation failed')
        if args.run_model:
            if args.offline_model:
                offline = fixture_server(output, args.expected_title, args.expected_bv, args.expected_date)
                config = json.loads(config_path.read_text(encoding='utf-8'))
                config['models'] = {'mode': 'merge', 'providers': {'recall-fixture': {
                    'baseUrl': f'http://127.0.0.1:{offline.server_port}/v1', 'apiKey': 'offline-fixture',
                    'api': 'openai-completions', 'models': [{'id': 'recall-fixture', 'name': 'Offline recall fixture',
                        'input': ['text'], 'reasoning': False}]}}}
                config['agents']['defaults']['model'] = {'primary': 'recall-fixture/recall-fixture'}
                config['agents']['defaults']['utilityModel'] = ''
                config['agents']['list'][0]['model'] = 'recall-fixture/recall-fixture'
                write_json(config_path, config)
            process = start_gateway(config_path, output)
            gateway = Gateway(config_path, output)
            gateway.env['OPENCLAW_HOME'] = str(config_path.parent / 'home')
            gateway.env['HOME'] = gateway.env['USERPROFILE'] = gateway.env['OPENCLAW_HOME']
            transcript = config_path.parent / 'agents/main/agent/openclaw-agent.sqlite'
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                status = gateway.call('mirally.memory.status', {}, tag='ready')
                if status.get('ready'):
                    break
                time.sleep(1)
            else:
                raise RuntimeError('Memory worker readiness timeout')
            report['checks']['maintenance_disabled'] = status.get('settings', {}).get('semantics', {}).get('enabled') is False
            # Public plugin diagnostics expose semantics but omit retention;
            # validate retention from the effective isolated reader profile.
            report['checks']['raw_retention_disabled'] = reader.config.raw_retention.enabled is False
            report['checks']['collector_not_started'] = status.get('worker', {}).get('coverage', {}).get('collector_started') is False
            if not all(report['checks'].values()):
                raise RuntimeError('Unexpected maintenance configuration')
            indexed = {question['kind']: (index, question) for index, question in
                enumerate(questions(args.expected_date, args.missing_date), 1)}
            for kind in args.cases:
                index, question = indexed[kind]
                turn = run_turn(gateway, transcript, question, index, args.timeout, dashboard=args.dashboard)
                turn['checks'] = (evaluate_natural(turn, candidates, args.expected_date, args.missing_date)
                    if question['kind'] == 'natural' else evaluate(turn, args.expected_title, args.expected_bv,
                    args.expected_date, args.missing_date, {row['id'] for row in records}))
                report['turns'].append(turn)
                write_json(output / 'report.json', report)
        report['passed'] = all(report['checks'].values()) and all(all(turn['checks'].values()) for turn in report['turns'])
        report['scope'] = ('offline-native-protocol-acceptance' if args.offline_model else 'native-model-acceptance') if args.run_model else 'snapshot-detail-only; model acceptance not run'
    except Exception as error:
        report.update(passed=False, error=f'{type(error).__name__}: {error}')
    finally:
        stop_isolated(process)
        if offline:
            offline.shutdown()
            offline.server_close()
        write_json(output / 'report.json', report)
    print(json.dumps({'passed': report['passed'], 'scope': report.get('scope'), 'report': str(output / 'report.json')}, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
