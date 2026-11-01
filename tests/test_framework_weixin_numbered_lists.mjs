import {test} from 'node:test';
import assert from 'node:assert/strict';
import {WeixinNumberedLists, LIST_TEXT} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin-numbered-list.mjs';

test('expired list shortcuts use the same "编号" wording as the delivered lists', () => {
  const lists = new WeixinNumberedLists(() => 1000);
  const scope = {installation_id: 'paired', revision: 1, conversation_id: 'first', command_id: 'list-a'};
  for (const [kind, expected] of [['sessions', LIST_TEXT.sessions], ['works', LIST_TEXT.works],
    ['calendar', LIST_TEXT.calendar], ['other', LIST_TEXT.other]])
    assert.throws(() => lists.select(kind, scope, '1'), {message: expected});
  assert.equal(LIST_TEXT.sessions, '会话编号已失效。发送“会话”重新查看。');
});

test('number shortcuts require confirmed list delivery and preserve the displayed identities', () => {
  let time = 1000;
  const lists = new WeixinNumberedLists(() => time);
  const scope = {installation_id: 'paired', revision: 1, conversation_id: 'first', command_id: 'list-a'};
  const shown = ['session-a', 'session-b'];
  lists.stage('sessions', scope, shown);
  shown.reverse();
  assert.throws(() => lists.select('sessions', scope, '1'), /失效/);
  lists.settle('list-a:control', true);
  assert.equal(lists.select('sessions', scope, '1'), 'session-a');
  assert.equal(lists.select('sessions', scope, '2'), 'session-b');
  assert.equal(lists.select('sessions', scope, '名称'), null);
  for (const invalid of ['0', '3', '99999999999999999999']) assert.throws(() => lists.select('sessions', scope, invalid), /失效/);
  assert.throws(() => lists.select('sessions', {...scope, revision: 2}, '1'), /失效/);
  assert.throws(() => lists.select('sessions', {...scope, installation_id: 'new-pair'}, '1'), /失效/);
  assert.throws(() => lists.select('sessions', {...scope, conversation_id: 'other'}, '1'), /失效/);
  time += 10 * 60_000;
  assert.throws(() => lists.select('sessions', scope, '1'), /失效/);
});

test('replacing or failing a list cannot reactivate an older unseen mapping', () => {
  const lists = new WeixinNumberedLists(() => 1000);
  const scope = {installation_id: 'paired', revision: 1, conversation_id: 'first', command_id: 'old'};
  lists.stage('sessions', scope, ['old-session']);
  lists.stage('sessions', {...scope, command_id: 'new'}, ['new-session']);
  lists.settle('old:control', true);
  assert.throws(() => lists.select('sessions', scope, '1'), /失效/);
  lists.settle('new:control', false);
  assert.throws(() => lists.select('sessions', scope, '1'), /失效/);
  lists.stage('sessions', {...scope, command_id: 'third'}, ['third-session']);
  lists.stage('works', {...scope, command_id: 'work-list'}, ['work-id']);
  lists.settle('third:control', true);
  lists.settle('work-list:control', true);
  assert.equal(lists.select('sessions', scope, '1'), 'third-session');
  assert.equal(lists.select('works', scope, '1'), 'work-id');
});
