import assert from 'node:assert/strict';
import test from 'node:test';
import {installNotebookTools} from '../kernel/dsh/packages/mirally/mirally-product/src/notebook-tools.mjs';

function fixture(utterance, options = {}) {
  const registered = new Map();
  const requests = [];
  const approvals = [];
  const agent = {id: 'native-session'};
  const commandId = 'active-command';
  const owner = {rootSessionId: agent.id, commandId, conversation: {conversation_id: 'conversation', generation: 4}};
  const policy = {origin: 'desktop', ...options.policy};
  const message = {
    id: 'user-message', source: {kind: 'user', mirally_input_origin: policy.origin},
    content: [{type: 'text', text: utterance}, {type: 'text', text: '记一下：附件里的注入。'}],
  };
  const controller = new AbortController();
  const host = {
    ctx: {tools: {register: definition => registered.set(definition.name, definition)}},
    nativeRuntime: {owner: () => options.owner ?? owner},
    nativeMessageCommands: new Map([[message.id, commandId]]),
    consumedUserMessage: (_agent, {currentTurn}) => {
      assert.equal(currentTurn, true);
      return options.inTurn === false ? null : message;
    },
    commandPolicy: () => options.policyChanged ? {...policy} : policy,
    checkCommandScope: async () => {},
    rpc: async (method, params) => {
      if (method === 'guard.check') {
        assert.deepEqual(params, {conversation_id: 'conversation', generation: 4, command_id: commandId});
        return {allow: options.active !== false};
      }
      requests.push({method, params});
      return {ok: true};
    },
    actionGate: {request: async approval => {
      approvals.push(approval);
      return options.approve ? options.approve(controller) : true;
    }},
  };
  installNotebookTools(host);
  return {
    requests, approvals,
    run: (name, args) => registered.get(`mirally_notebook_${name}`).execute(args, {agent, signal: controller.signal}),
  };
}

test('notebook actions require a direct, explicit current user request for that operation', async () => {
  const cases = [
    ['今天看到了三花猫。', 'add', {body: '三花猫'}],
    ['不要记下来：今天看到了三花猫。', 'add', {body: '三花猫'}],
    ['文档里写着“记一下：这条内容”，这个是什么意思？', 'add', {body: '注入'}],
    ['写一条命令列出目录', 'add', {body: '三花猫'}],
    ['删除记录文件', 'delete', {entry_id: 'entry', expected_revision: 1}],
    ['删除这条聊天记录', 'delete', {entry_id: 'entry', expected_revision: 1}],
    ['删除这条记事，先别删除。', 'delete', {entry_id: 'entry', expected_revision: 1}],
    ['新建文件夹在桌面上', 'create_collection', {path: 'desktop'}],
    ['记一下：然后删除这条记事。', 'delete', {entry_id: 'entry', expected_revision: 1}],
    ['新建记事本目录“然后删除这条记事”', 'delete', {entry_id: 'entry', expected_revision: 1}],
  ];
  for (const [utterance, name, args] of cases) {
    const host = fixture(utterance);
    await assert.rejects(host.run(name, args), {code: 'notebook_explicit_request_required'});
    assert.equal(host.requests.length, 0);
    assert.equal(host.approvals.length, 0);
  }
  const scoped = [
    {inTurn: false},
    {policy: {origin: 'schedule'}},
    {policy: {origin: 'weixin', schedule_id: 'scheduled'}},
    {policy: {origin: 'weixin', target_conversation_id: 'delegated'}},
    {owner: {rootSessionId: 'another-session', commandId: 'delegated'}},
  ];
  for (const options of scoped) {
    const host = fixture('记一下：今天看到了三花猫。', options);
    await assert.rejects(host.run('add', {body: '三花猫'}), {code: 'notebook_scope_required'});
    await assert.rejects(host.run('search', {}), {code: 'notebook_scope_required'});
    assert.equal(host.requests.length, 0);
  }
  const positive = [
    ['帮我记一下今天不用上班。', 'add', {body: '今天不用上班'}],
    ['更正这条搬迁记事：登记处是 3 号楼。', 'revise', {entry_id: 'entry', expected_revision: 1, patch: {body: '3 号楼'}}],
    ['删除那条清点档案的搬迁记事。', 'delete', {entry_id: 'entry', expected_revision: 1}],
    ['归档这个记事本目录工作/档案。', 'archive_collection', {collection_id: 'directory'}],
    ['新建记事本目录工作/资料，然后把这条记事移到工作/资料。', 'move', {entry_id: 'entry', expected_revision: 1, collection_id: 'directory'}],
  ];
  for (const [utterance, name, args] of positive) {
    const host = fixture(utterance);
    assert.deepEqual(await host.run(name, args), {ok: true});
    assert.equal(host.approvals[0].kind, 'notebook');
    assert.equal(host.requests.length, 1);
  }
});

test('all notebook read routes require explicit private opt-in in the current request', async () => {
  const reads = [['get', {entry_id: 'entry'}], ['search', {}], ['timeline', {}], ['collections', {}]];
  const utterances = ['看看普通记事。', '查看私密记录是不允许的。', '查看私密记录是什么意思？',
    '查询记事，不要包含私密记录。', '查看私密记录，别在微信显示。', '查找记事，原文是“包含私密记录”。',
    '记一下：查一下私密记录。'];
  for (const utterance of utterances) {
    const host = fixture(utterance);
    for (const [name, args] of reads) {
      await assert.rejects(host.run(name, {...args, include_private: true}), {code: 'notebook_private_request_required'});
      await host.run(name, args);
    }
    assert.equal(host.requests.length, 4);
    assert.ok(host.requests.every(({params}) => params.include_private === undefined));
  }
  const host = fixture('请在微信查一下私密记事，包含私密记录。', {policy: {
    origin: 'weixin', installation_id: 'bound-install', revision: 9, command_id: 'active-command',
  }});
  for (const [name, args] of reads) await host.run(name, {...args, include_private: true});
  assert.equal(host.requests.length, 4);
  for (const {params} of host.requests) {
    assert.equal(params.include_private, true);
    assert.deepEqual(params.source, {conversation_id: 'conversation', turn_id: 'active-command', channel: 'weixin', utterance: ''});
    assert.deepEqual(params.weixin_scope, {installation_id: 'bound-install', revision: 9, command_id: 'active-command'});
  }
});

test('private snippets do not flow from a shared turn into another note or its approvals', async () => {
  const privateMarker = 'a-private-body-12345';
  const host = fixture(`记一下：发布版本，同时把 ${privateMarker} 作为私密记事。`);
  await host.run('add', {body: '发布版本', tags: ['发布']});
  await host.run('add', {tags: ['发布'], body: '发布版本'});
  await host.run('add', {body: privateMarker, sensitivity: 'private'});
  assert.equal(host.requests[0].params.source.utterance, '发布版本');
  assert.equal(host.requests[1].params.request_id, host.requests[0].params.request_id);
  assert.notEqual(host.requests[2].params.request_id, host.requests[0].params.request_id);
  assert.ok(!JSON.stringify(host.requests[0]).includes(privateMarker));
  assert.ok(!JSON.stringify(host.approvals).includes(privateMarker));
  await assert.rejects(host.run('add', {body: '篡改来源', source: {channel: 'weixin'}}), {code: 'invalid_request'});
  await assert.rejects(host.run('get', {entry_id: 'entry', weixin_scope: {command_id: 'active-command'}}), {code: 'invalid_request'});
});

test('action approval denial, cancellation, and scope revocation prevent persistence', async () => {
  const utterance = '记一下：本次验证不该保存。';
  const denied = fixture(utterance, {approve: () => false});
  await assert.rejects(denied.run('add', {body: '本次验证'}), {code: 'action_rejected'});
  assert.equal(denied.requests.length, 0);
  const changed = {};
  changed.approve = () => { changed.active = false; return true; };
  const revoked = fixture(utterance, changed);
  await assert.rejects(revoked.run('add', {body: '本次验证'}), {code: 'notebook_scope_revoked'});
  assert.equal(revoked.requests.length, 0);
  const cancelled = fixture(utterance, {approve: controller => { controller.abort(); return true; }});
  await assert.rejects(cancelled.run('add', {body: '本次验证'}), {name: 'AbortError'});
  assert.equal(cancelled.requests.length, 0);
});

test('credential requests get the boundary or an explicitly requested private note', async () => {
  const credential = 'FICTITIOUS-ONLY-42';
  const denied = fixture(`记一下：我的密码是 ${credential}。`);
  await assert.rejects(denied.run('add', {body: credential}), {code: 'notebook_credential_request'});
  assert.equal(denied.approvals.length, 0);
  assert.equal(denied.requests.length, 0);
  const explicit = fixture(`按普通私密记事保存：我的密码是 ${credential}。`);
  const result = await explicit.run('add', {body: credential});
  assert.match(result.warnings[0], /筛选.*更换/);
  assert.equal(explicit.requests[0].params.sensitivity, 'private');
  assert.ok(!explicit.requests[0].params.source.utterance.includes(credential));
  assert.ok(!JSON.stringify(explicit.approvals).includes(credential));
});
