import test from 'node:test';
import assert from 'node:assert/strict';
import {notifyReminders,canDeliverReminder,ackReminder} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin-reminders.mjs';

function fixture() {
  const state={bound:true,enabled:true,context_token:'context',installation_id:'account',revision:3,conversation_id:'conversation'};
  let item={delivery_id:'r1',status:'pending',title:'会议',body:'明天开会',eligible:true};
  const sent=[]; const calls=[];
  const channel={closed:false,phase:'connected',state:async()=>state,authorize:async()=>{},
    product:{rpc:async(method,args)=>{
      calls.push({method,args});
      if(method==='calendar.delivery.list')return {deliveries:[item]};
      if(method==='calendar.delivery.claim') {item={...item,status:'sending',claim_token:'token',destination:args.destination};return {delivery:item,should_send:true,claim_token:'token'};}
      if(method==='calendar.delivery.get')return {delivery:item};
      if(method==='calendar.delivery.ack'){item={...item,status:args.status};return {delivery:item};}
      throw Error(method);
    }},store:async(method,args)=>{assert.equal(method,'enqueue');sent.push(args);return {delivery:{key:args.key,status:'queued'}};}};
  return {channel,state,sent,calls,get item(){return item;},set item(value){item=value;}};
}

test('reminder claims the bound destination and only acknowledges the outbox result',async()=>{
  const f=fixture(); await notifyReminders(f.channel);
  assert.equal(f.sent.length,1); assert.equal(f.sent[0].key,'calendar-reminder:r1');
  assert.match(f.sent[0].text,/会议/); assert.equal(f.item.status,'sending');
  const delivery={key:'calendar-reminder:r1',installation_id:'account',revision:3};
  assert.equal(await canDeliverReminder(f.channel,delivery),true);
  assert.equal(await canDeliverReminder(f.channel,{...delivery,revision:4}),false);
  await ackReminder(f.channel,delivery,'sent'); assert.equal(f.item.status,'sent');
  await notifyReminders(f.channel); assert.equal(f.sent.length,1);
});

test('unbound channels do not claim, and revoked reminders cannot be delivered',async()=>{
  const f=fixture(); f.state.bound=false; await notifyReminders(f.channel); assert.equal(f.calls.length,0);
  f.state.bound=true; await notifyReminders(f.channel);
  f.item={...f.item,eligible:false};
  assert.equal(await canDeliverReminder(f.channel,{key:'calendar-reminder:r1',installation_id:'account',revision:3}),false);
  assert.equal(await canDeliverReminder(f.channel,{key:'other'}),true);
});

test('an uncertain enqueue is recorded as unknown rather than resent',async()=>{
  const f=fixture(); f.channel.store=async()=>{throw new Error('connection lost');};
  await assert.rejects(notifyReminders(f.channel),/connection lost/);
  assert.equal(f.item.status,'unknown');
  await notifyReminders(f.channel); assert.equal(f.calls.filter(call=>call.method==='calendar.delivery.claim').length,1);
});
