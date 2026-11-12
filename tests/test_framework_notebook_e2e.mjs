import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdtemp, readFile, realpath, rm, writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {basename, dirname, join} from 'node:path';
import {test} from 'node:test';
import {setTimeout as delay} from 'node:timers/promises';
import {fileURLToPath, pathToFileURL} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const homePrefix = 'mirally notebook 中文 ';

async function until(check, label, timeout = 25000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const result = await check();
    if (result) return result;
    await delay(60);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

function sendModelReply(response, delta, finish) {
  response.writeHead(200, {'content-type': 'text/event-stream'});
  const frames = [
    {id: 'fixture', choices: [{index: 0, delta, finish_reason: null}]},
    {id: 'fixture', choices: [{index: 0, delta: {}, finish_reason: finish}],
      usage: {prompt_tokens: 20, completion_tokens: 10, total_tokens: 30}},
  ];
  for (const frame of frames) response.write(`data: ${JSON.stringify(frame)}\n\n`);
  response.end('data: [DONE]\n\n');
}

function toolText(message) {
  return typeof message.content === 'string' ? message.content : message.content.map(item => item.text).join('');
}

const call = (name, args) => ({name: `mirally_notebook_${name}`, args});

test('desktop and Weixin share the private, editable Mirally notebook', {timeout: 180000}, async t => {
  const home = await mkdtemp(join(tmpdir(), homePrefix));
  const inbox = [];
  const replies = [];
  const fixtureErrors = [];
  const turns = [];
  let child, runtime, logs = '';

  function weixinResponse(url, body) {
    if (url.startsWith('/ilink/bot/get_bot_qrcode')) return {qrcode: 'fixture', qrcode_img_content: 'fixture-scan'};
    if (url.startsWith('/ilink/bot/get_qrcode_status')) {
      return {status: 'confirmed', bot_token: 'fixture-token', ilink_bot_id: 'bot', ilink_user_id: 'owner'};
    }
    if (url === '/ilink/bot/getupdates') return {ret: 0, msgs: inbox.splice(0), get_updates_buf: 'cursor'};
    if (url === '/ilink/bot/getconfig') return {ret: 0, typing_ticket: 'fixture-ticket'};
    if (url === '/ilink/bot/sendmessage') {
      for (const item of JSON.parse(body).msg.item_list) if (item.text_item) replies.push(item.text_item.text);
      return {ret: 0};
    }
    if (['/ilink/bot/sendtyping', '/ilink/bot/msg/notifystart', '/ilink/bot/msg/notifystop'].includes(url)) return {ret: 0};
    throw new Error(`Unexpected iLink request ${url}`);
  }

  const server = createServer(async (request, response) => {
    try {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = Buffer.concat(chunks).toString();
      if (request.url.startsWith('/ilink/')) {
        if (request.url === '/ilink/bot/getupdates') await delay(100);
        response.setHeader('content-type', 'application/json');
        response.end(JSON.stringify(weixinResponse(request.url, body)));
        return;
      }

      assert.equal(request.url, '/v1/chat/completions');
      const input = JSON.parse(body);
      const userIndex = input.messages.findLastIndex(message => message.role === 'user'
        && turns.some(item => JSON.stringify(message.content).includes(item.text)));
      const content = JSON.stringify(input.messages[userIndex]?.content);
      assert.ok(content, 'Model request is missing the planned user message');
      const turn = turns.findLast(item => content.includes(item.text));
      if (turn.id === 'notebook-1') {
        const prompt = input.messages.filter(message => ['system', 'user'].includes(message.role)).map(toolText).join('\n');
        assert.match(prompt, /Use the notebook for records the user explicitly asks to save or change/);
        assert.match(prompt, /"calendar_clock":\{[^}]*"time_zone":"Asia\/Shanghai","confirmed":true/);
      }
      const results = input.messages.slice(userIndex + 1).filter(message => message.role === 'tool');
      const step = turn.calls[results.length];
      if (!step) {
        assert.equal(results.length, turn.calls.length);
        turn.results = results.map(toolText);
        sendModelReply(response, {content: turn.receipt}, 'stop');
        return;
      }

      const operation = typeof step === 'function' ? step(results.map(item => JSON.parse(toolText(item)))) : step;
      assert.ok(input.tools.some(tool => tool.function.name === operation.name), `Model lacks ${operation.name}`);
      const toolCall = {index: 0, id: `${turn.id}-${results.length}`, type: 'function', function: {
        name: operation.name, arguments: JSON.stringify(operation.args),
      }};
      sendModelReply(response, {tool_calls: [toolCall]}, 'tool_calls');
    } catch (error) {
      fixtureErrors.push(error);
      response.writeHead(500);
      response.end('fixture error');
    }
  });

  async function stopHost() {
    if (!child || child.exitCode !== null || child.signalCode !== null) return;
    const exited = once(child, 'exit');
    child.stdin.end();
    if (!await Promise.race([exited, delay(7000, false, {ref: false})])) {
      child.kill();
      await exited;
    }
  }

  t.after(async () => {
    await stopHost();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    const canonical = await realpath(home);
    assert.equal(dirname(canonical), await realpath(tmpdir()));
    assert.ok(basename(canonical).startsWith(homePrefix));
    await rm(canonical, {recursive: true, force: true});
  });

  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const endpoint = `http://127.0.0.1:${server.address().port}`;
  const preload = join(home, 'fixture-model.mjs');
  const models = pathToFileURL(join(root, 'kernel/dsh/packages/mirally/mirally-product/src/models.mjs')).href;
  // Use the fixture provider without replacing any product tool or Weixin dispatch code.
  await writeFile(preload, `import {ProductModels} from ${JSON.stringify(models)};
const {sync, requireOptions} = ProductModels.prototype;
ProductModels.prototype.sync = function(settings) {
  if (this.testMode && !this.fixture) {
    this.registration?.();
    this.fixture = new ProductModels(this.ctx, {testMode: false});
  }
  if (this.fixture) return this.options = sync.call(this.fixture, settings);
  return sync.call(this, settings);
};
ProductModels.prototype.requireOptions = function(...args) {
  return requireOptions.apply(this.fixture ?? this, args);
};
`);

  async function startHost() {
    child = spawn(process.execPath, ['--import', pathToFileURL(preload).href,
      join(root, 'kernel/dsh/packages/mirally/mirally-product/start.mjs'), '--root', root, '--home', home,
      '--python', join(root, '.venv/Scripts/python.exe'), '--test-mode'], {
      cwd: root, windowsHide: true,
      env: {...process.env, PYTHONUTF8: '1', MIRALLY_TEST_WEIXIN_URL: endpoint},
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    child.stdout.on('data', chunk => { logs += chunk; });
    child.stderr.on('data', chunk => { logs += chunk; });
    runtime = await wait(async () => {
      if (child.exitCode !== null) throw new Error(`Host exited with ${child.exitCode}`);
      const current = await readFile(join(home, 'runtime.json'), 'utf8').catch(error => {
        if (error.code === 'ENOENT') return 'null';
        throw error;
      });
      const value = JSON.parse(current);
      return value?.pid === child.pid && value;
    }, 'host startup');
  }

  async function wait(check, label) {
    try {
      return await until(() => {
        if (fixtureErrors.length) throw fixtureErrors[0];
        return check();
      }, label);
    } catch (error) {
      error.message += `\n${logs}`;
      throw error;
    }
  }

  async function api(path, body, expected = 200) {
    const response = await fetch(runtime.url + path, {
      method: 'POST', headers: {authorization: `Bearer ${runtime.token}`, 'content-type': 'application/json'},
      body: JSON.stringify(body),
    });
    const data = await response.text();
    assert.equal(response.status, expected, data);
    return JSON.parse(data);
  }

  const rpc = async (method, params = {}) => (await api('/api/rpc', {method, params})).result;
  const newSession = title => api('/api/sessions', {title}, 201);

  function plan(text, calls) {
    const id = `notebook-${turns.length + 1}`;
    const turn = {id, text, calls, receipt: `记事验收回执 ${id}。`};
    turns.push(turn);
    return turn;
  }

  async function desktop(session, text, calls, approve) {
    const turn = plan(text, calls);
    await api(`/api/sessions/${session.id}/messages`, {commandId: turn.id, text}, 202);
    if (approve !== undefined) {
      const approval = await wait(async () => (await rpc('action.approvals.list', {conversation_id: session.id}))
        .approvals.find(item => item.command_id === turn.id), `approval for ${turn.id}`);
      await rpc('action.approvals.decide', {approval_id: approval.id, conversation_id: session.id, allow: approve});
    }
    await wait(async () => {
      const {commands} = await rpc('command.list', {conversation_id: session.id, command_id: turn.id});
      return commands.some(command => command.state === 'completed') && turn.results;
    }, `${turn.id} (${text})`);
    return turn.results;
  }

  async function weixin(text, calls) {
    const turn = plan(text, calls);
    inbox.push({message_type: 1, message_state: 2, message_id: String(turns.length), from_user_id: 'owner',
      to_user_id: 'bot', create_time_ms: Date.now(), context_token: 'fixture-context',
      item_list: [{type: 1, text_item: {text}}]});
    await wait(() => replies.some(reply => reply.includes(turn.receipt)), `Weixin reply to ${text}`);
    return turn.results;
  }

  const decode = results => results.map(result => {
    assert.ok(!result.startsWith('Error:'), result);
    return JSON.parse(result);
  });
  await startHost();
  await rpc('settings.update', {patch: {
    background_enabled: false,
    model: {provider: 'deepseek', api_format: 'openai-chat', base_url: `${endpoint}/v1`, api_key: 'fixture',
      model_id: 'fixture', reasoning_effort: 'off', max_tokens: 1024},
  }});
  await rpc('calendar.settings.update', {time_zone: 'Asia/Shanghai'});
  const firstSession = await newSession('最初的记事会话');

  // S1: record separately from event time, receive an ID, and keep the note unfiled.
  const cat = {title: '梅坞老巷的三花猫', body: '在梅坞老巷看到一只三花猫，很亲人', tags: ['猫', '梅坞老巷'],
    event_time: '2026-05-01', time_quality: 'day', type: 'note'};
  const [catReceipt] = decode(await desktop(firstSession, '记一下：5月1日在梅坞老巷看到一只三花猫，很亲人。',
    [call('add', cat)], true));
  assert.ok(catReceipt.entry_id);
  assert.equal(catReceipt.collection_id, null);
  assert.equal(catReceipt.revision, 1);
  assert.deepEqual(catReceipt.suggested_tags.toSorted(), cat.tags.toSorted());
  const [catDetail] = decode(await desktop(firstSession, '查询刚才的三花猫记事。', [call('get', {entry_id: catReceipt.entry_id})]));
  assert.equal(catDetail.entry.body, cat.body);
  assert.deepEqual(catDetail.entry.tags.toSorted(), cat.tags.toSorted());
  assert.equal(catDetail.entry.event_time, cat.event_time);
  assert.equal(catDetail.entry.time_quality, 'day');
  assert.ok(Math.abs(Date.now() - catDetail.entry.recorded_at) < 60000);
  assert.equal(catDetail.entry.source.channel, 'desktop');
  assert.equal(catDetail.entry.source.conversation_id, firstSession.id);

  const blocked = {body: '误记门槛样本'};
  await desktop(firstSession, '记一下：误记门槛样本。', [call('add', blocked)], false);
  await rpc('settings.update', {patch: {action_approval_mode: 'trusted'}});
  const [writeDenied] = await desktop(firstSession, '随便聊聊误记门槛样本是什么。', [call('add', blocked)]);
  assert.match(writeDenied, /明确|用户|确认|explicit/i);
  const [blockedSearch] = decode(await desktop(firstSession, '查找误记门槛样本的记事。', [call('search', {query: blocked.body})]));
  assert.equal(blockedSearch.total, 0);

  // S2/S6: the third related note creates a directory; identical tool retries within a turn are idempotent.
  const migration = (body, fields = {}) => ({body, tags: ['搬迁'], ...fields});
  const [sourceNote] = decode(await desktop(firstSession, '记一下：老周说五月第3周搬迁工位。',
    [call('add', migration('老周说五月第3周搬迁工位', {event_time: '2026-05-05', time_quality: 'day'}))]));
  await desktop(firstSession, '记一下：搬迁需要先清点档案。', [call('add', migration('搬迁需要先清点档案'))]);
  const thirdNote = '搬迁登记处在 2 号楼';
  const thirdRequest = `记一下：${thirdNote}。`;
  const autoFile = call('add', migration(thirdNote, {collection_path: '工作/搬迁'}));
  const [third, duplicate] = decode(await desktop(firstSession, thirdRequest, [autoFile, autoFile]));
  const [migrationFolders] = decode(await desktop(firstSession, '查一下刚才自动归类的搬迁记事目录。',
    [call('collections', {collection_id: third.collection_id})]));
  const migrationFolder = migrationFolders.collection;
  assert.equal(migrationFolder.path, '工作/搬迁');
  assert.equal(migrationFolder.kind, 'auto_created');
  assert.equal(duplicate.replayed, true);
  assert.equal(duplicate.entry_id, third.entry_id);
  const [fourth] = decode(await desktop(firstSession, thirdRequest, [autoFile]));
  assert.notEqual(fourth.entry_id, third.entry_id);

  // S3/S9: count before pagination, then retrieve evidence after restart and clearing the isolated memory database.
  const [count] = decode(await desktop(firstSession, '一共记过多少条搬迁相关的事？', [call('search', {tags: ['搬迁'], limit: 1})]));
  assert.equal(count.total, 4);
  assert.equal(count.entries.length, 1);
  await stopHost();
  const isolatedMemory = join(home, 'memory', 'memory.sqlite3');
  for (const suffix of ['', '-shm', '-wal']) await rm(isolatedMemory + suffix, {force: true});
  await startHost();
  const session = await newSession('重启后另一个桌面会话');
  const [evidence] = decode(await desktop(session, '上次老周说搬工位是哪周？查一下记事本。', [call('search', {query: '老周'})]));
  assert.equal(evidence.total, 1);
  assert.match(evidence.entries[0].excerpt, /第3周/);
  assert.equal(evidence.entries[0].event_time, '2026-05-05');
  assert.equal(evidence.entries[0].entry_id, sourceNote.entry_id);

  // Bind a different conversation so access cannot depend on sharing the desktop transcript.
  const remoteSession = await newSession('微信验收会话');
  await rpc('weixin.login.start');
  const login = await wait(async () => {
    const state = await rpc('weixin.status');
    return state.state === 'awaiting_confirmation' && state;
  }, 'iLink login');
  await rpc('weixin.bind', {expected_revision: login.revision, conversation_id: remoteSession.id});
  await rpc('settings.update', {patch: {weixin_remote_control_enabled: true, weixin_auto_approve_enabled: true}});
  const [remoteEvidence] = decode(await weixin('从微信查一下老周的记事。', [call('search', {query: '老周'})]));
  assert.equal(remoteEvidence.entries[0].entry_id, evidence.entries[0].entry_id);

  // S4: correct a desktop record in Weixin and verify its previous value on desktop.
  const [corrected] = decode(await weixin('更正这条记事：搬迁登记处是 3 号楼。', [call('revise', {
    entry_id: third.entry_id, expected_revision: 1, patch: {body: '搬迁登记处在 3 号楼'}, reason: '楼号写错',
  })]));
  assert.equal(corrected.revision, 2);
  const [correctedDetail] = decode(await desktop(session, '查看已经更正的搬迁登记处记事和修改记录。',
    [call('get', {entry_id: third.entry_id})]));
  assert.equal(correctedDetail.entry.body, '搬迁登记处在 3 号楼');
  assert.equal(correctedDetail.revisions[0].source.channel, 'weixin');
  assert.match(JSON.stringify(correctedDetail.revisions), /2 号楼/);

  // S5a/S5b: terminal entries disappear from normal search and retain their revisions.
  await desktop(session, '撤销这条记事：我没说过登记处在 3 号楼。',
    [call('reverse', {entry_id: third.entry_id, expected_revision: 2, reason: '误记'})]);
  const [foundForDeletion] = decode(await desktop(session, '删除那条记事：先清点档案。', [
    call('search', {query: '先清点档案'}),
    ([found]) => call('delete', {entry_id: found.entries[0].entry_id, expected_revision: 1, reason: '用户要求'}),
  ]));
  assert.equal(foundForDeletion.total, 1);
  const deleted = foundForDeletion.entries[0].entry_id;
  const [visible, removed] = decode(await desktop(session, '查看剩下的搬迁记事和刚删掉的记录。',
    [call('search', {tags: ['搬迁']}), call('get', {entry_id: deleted})]));
  assert.equal(visible.total, 2);
  assert.ok(!visible.entries.some(item => [third.entry_id, deleted].includes(item.entry_id)));
  assert.equal(removed.entry.status, 'deleted');
  assert.ok(removed.revisions.length > 0);
  const [reversed] = decode(await desktop(session, '查看刚才撤销记事的修改记录。', [call('get', {entry_id: third.entry_id})]));
  assert.equal(reversed.entry.status, 'reversed');
  assert.ok(reversed.revisions.length > 0);

  // S7/S7b: a private-only collection and its entries must stay hidden at every retrieval entry point.
  const privateMarker = '110105199001011234';
  const [privateNote] = decode(await weixin(`记一下：证件号示例 ${privateMarker}。`, [
    call('add', {body: `证件号示例 ${privateMarker}`, collection_path: '证件备忘'}),
  ]));
  const privateFolder = privateNote.collection;
  assert.equal(privateNote.sensitivity, 'private');
  const [unfiledPrivate] = decode(await desktop(session, '记一下：把私人生日备忘作为私密记事。', [
    call('add', {body: '私人生日备忘', sensitivity: 'private'}),
  ]));
  assert.equal(unfiledPrivate.sensitivity, 'private');
  const defaultReads = [call('search', {query: privateMarker}), call('timeline', {}), call('collections', {}),
    call('get', {entry_id: privateNote.entry_id})];
  for (const read of [desktop.bind(null, session), weixin]) {
    const [searchRaw, timelineRaw, collectionsRaw, direct] = await read('查看记事本的普通记事及目录。', defaultReads);
    const [search, timeline, collections] = decode([searchRaw, timelineRaw, collectionsRaw]);
    assert.equal(search.total, 0);
    assert.ok(timeline.entries.some(item => item.entry_id === catReceipt.entry_id));
    assert.ok(!timeline.entries.some(item => item.entry_id === privateNote.entry_id));
    assert.ok(!collections.collections.some(item => item.collection_id === privateFolder.collection_id));
    assert.ok(!collections.entries.some(item => item.entry_id === unfiledPrivate.entry_id));
    assert.ok(![searchRaw, timelineRaw, collectionsRaw, direct].some(result => result.includes(privateMarker)));
  }
  const privateReads = [call('search', {query: privateMarker, include_private: true}),
    call('timeline', {include_private: true}), call('collections', {include_private: true}),
    call('get', {entry_id: privateNote.entry_id, include_private: true})];
  for (const [channel, read] of [['电脑', desktop.bind(null, session)], ['微信', weixin]]) {
    const [found, timeline, collections, detail] = decode(await read('请查一下私密记事，包含私密记录。', privateReads));
    assert.equal(found.total, 1);
    assert.equal(found.entries[0].entry_id, privateNote.entry_id);
    assert.ok(timeline.entries.some(item => item.entry_id === privateNote.entry_id));
    assert.ok(collections.collections.some(item => item.collection_id === privateFolder.collection_id));
    assert.ok(collections.entries.some(item => item.entry_id === unfiledPrivate.entry_id));
    assert.match(detail.entry.body, new RegExp(privateMarker));
    const [overreach] = await read(`请在${channel}再查一遍普通记事。`, [call('search', {query: privateMarker, include_private: true})]);
    assert.ok(!overreach.includes(privateMarker));
  }

  // S10/S11: move, rename, and archive while keeping the active note accessible.
  const [destination] = decode(await desktop(session, '新建记事本目录：工作/资料。',
    [call('create_collection', {path: '工作/资料'})]));
  const [moved] = decode(await desktop(session, '把这条记事移到工作/资料。',
    [call('move', {entry_id: fourth.entry_id, expected_revision: 1, collection_id: destination.collection_id})]));
  assert.equal(moved.revision, 2);
  const [oldFolder, newFolder] = decode(await desktop(session, '分别查找旧目录和新目录的记事。', [
    call('search', {collection_id: migrationFolder.collection_id}), call('search', {collection_id: destination.collection_id}),
  ]));
  assert.equal(oldFolder.total, 0);
  assert.equal(newFolder.entries[0].entry_id, fourth.entry_id);
  await desktop(session, '重命名记事本目录：工作/资料改为工作/档案。',
    [call('rename_collection', {collection_id: destination.collection_id, title: '档案'})]);
  const [renamed] = decode(await desktop(session, '查看刚刚改名的记事本目录。', [call('collections', {collection_id: destination.collection_id})]));
  assert.equal(renamed.collection.path, '工作/档案');
  await desktop(session, '归档记事本目录：工作/档案。', [call('archive_collection', {collection_id: destination.collection_id})]);
  const [unfiled, archived] = decode(await desktop(session, '查一下归档目录里的原记事和目录。', [
    call('get', {entry_id: fourth.entry_id}), call('collections', {collection_id: destination.parent_id}),
  ]));
  assert.equal(unfiled.entry.collection, null);
  assert.equal(unfiled.entry.status, 'active');
  assert.ok(!archived.collections.some(item => item.collection_id === destination.collection_id));

  // S12: roll back a mistaken automatically created folder and its mistaken note.
  const [wrong] = decode(await desktop(session, '记一下：整理时误录了试验楼号。', [
    call('add', {body: '误录了试验楼号', collection_path: '工作/误建'}),
  ]));
  const [updated] = decode(await desktop(session, '更正这条记事：试验楼号为 9 号楼。', [
    call('revise', {entry_id: wrong.entry_id, expected_revision: 1, patch: {body: '更正试验楼号为 9 号楼'}}),
  ]));
  await desktop(session, '删除这条记事：试验楼号。', [
    call('delete', {entry_id: wrong.entry_id, expected_revision: updated.revision}),
  ]);
  await desktop(session, '归档记事本目录：工作/误建。', [
    call('archive_collection', {collection_id: wrong.collection_id}),
  ]);
  const [afterCorrection, afterArchive] = decode(await desktop(session, '检查误建目录和误录的试验楼号记事是否还有可见结果。', [
    call('search', {query: '试验楼号'}), call('collections', {collection_id: wrong.collection.parent_id}),
  ]));
  assert.equal(afterCorrection.total, 0);
  assert.ok(!afterArchive.collections.some(item => item.collection_id === wrong.collection_id));
  assert.deepEqual(fixtureErrors, []);
});
