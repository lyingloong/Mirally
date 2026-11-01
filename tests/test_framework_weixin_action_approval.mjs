import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ActionGate} from '../kernel/dsh/packages/mirally/mirally-product/src/action-gate.mjs';
import {WeixinChannel} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin.mjs';

async function setup({failPart = -1} = {}) {
  const state = {installation_id:'installation', revision:1, bound:true, enabled:true, conversation_id:'conversation',
    peer_id:'owner', bot_id:'bot', token:'token', context_token:'context', base_url:'https://ilinkai.weixin.qq.com'};
  const scope = {origin:'weixin', installation_id:'installation', revision:1, conversation_id:'conversation', command_id:'command'};
  const agent = {}, rows = [], sent = [];
  let command = 'command';
  const host = {commandPolicies:new Map([['command',scope]]), handles:new Map([['native',{agent}]]),
    conversationForSession:() => ({conversation_id:'conversation'}), consumedCommandId:() => command,
    checkCommandScope:async () => {}, guard:async () => {}, broadcast:() => {},
    rpc:async (method, p = {}) => {
      if (method === 'channel.weixin.get') return {state:{...state}};
      if (method === 'channel.weixin.admit') return {event:{...p, conversation_id:state.conversation_id, command_id:`event-${p.event_id}`}};
      if (method === 'channel.weixin.event') return {};
      if (method === 'channel.weixin.enqueue') { const row = {...p, delivery_id:'delivery', status:'pending'}; rows.push(row); return {delivery:row}; }
      if (method === 'channel.weixin.outbox') return {deliveries:rows.filter(row => row.status === 'pending')};
      if (method === 'channel.weixin.claim') {const row=rows.find(r=>r.delivery_id===p.delivery_id); row.status='sending'; return {delivery:{...row,state:{...state}}};}
      if (method === 'channel.weixin.ack') {const row=rows.find(r=>r.delivery_id===p.delivery_id); Object.assign(row,{status:p.status,receipt:p.receipt}); return {delivery:row};}
      throw Error(method);
    }};
  host.actionGate = new ActionGate(host);
  host.weixin = new WeixinChannel(host, {apiFactory:()=>({sendText:async ({text})=>{
    if (sent.length === failPart) throw Error('unknown network failure');
    sent.push(text);
  }})});
  host.weixin.connection = new AbortController();
  return {state, scope, host, rows, sent, setCommand: v => command=v};
}

const tick = () => new Promise(resolve => setTimeout(resolve, 20));
test('微信审批完整分段送达后才能确认，且绑定身份、会话与回合', async () => {
  const f = await setup(), text='命令😀'.repeat(6000);
  const result=f.host.actionGate.request({sessionId:'native',commandId:'command',kind:'shell',title:'命令',detail:text});
  await tick();
  assert.equal(f.rows[0].status,'sent');
  const assembled=f.sent.map(s=>s.replace(/^（\d+\/\d+） /,'')).join('');
  assert.ok(assembled.includes(text));
  const code=assembled.match(/\/approve ([0-9]{9})/)[1];
  await assert.rejects(f.host.actionGate.decideRemote({...f.scope, revision:2},code,true));
  await assert.rejects(f.host.actionGate.decideRemote({...f.scope, conversation_id:'elsewhere'},code,true));
  f.setCommand('another');
  await assert.rejects(f.host.actionGate.decideRemote(f.scope,code,true));
  f.setCommand('command');
  await f.host.actionGate.decideRemote(f.scope,code,true);
  assert.equal(await result,true);
  await assert.rejects(f.host.actionGate.decideRemote(f.scope,code,true));
});

test('部分投递或结果未知时不能确认', async () => {
  const f=await setup({failPart:1});
  const result=f.host.actionGate.request({sessionId:'native',commandId:'command',kind:'shell',title:'命令',detail:'x'.repeat(12000)});
  await tick();
  assert.equal(f.rows[0].status,'unknown');
  const code=f.rows[0].text.match(/\/approve ([0-9]{9})/)[1];
  await assert.rejects(f.host.actionGate.decideRemote(f.scope,code,true));
  f.host.actionGate.dispose();
  assert.equal(await result,false);
});

test('只处理绑定微信账号发来的确认消息', async () => {
  const f=await setup();
  const result=f.host.actionGate.request({sessionId:'native',commandId:'command',kind:'shell',title:'命令',detail:'echo test'});
  await tick();
  const code=f.rows[0].text.match(/\/approve ([0-9]{9})/)[1];
  const message={message_type:1,message_state:2,message_id:'1',from_user_id:'stranger',to_user_id:'bot',
    create_time_ms:Date.now(),context_token:'context',item_list:[{type:1,text_item:{text:`/approve ${code}`}}]};
  await f.host.weixin.receive({...f.state},message);
  assert.equal(f.host.actionGate.list('conversation').approvals.length,1);
  await f.host.weixin.receive({...f.state},{...message,message_id:'2',from_user_id:'owner'});
  assert.equal(await result,true);
});
