import {test} from 'node:test';
import assert from 'node:assert/strict';
import {installDesktopObservation} from '../kernel/dsh/packages/mirally/mirally-product/src/desktop-observation.mjs';

function fixture() {
  const registered = new Map(), approvals = [], observations = [], saved = [];
  const attachment = {id:'image'};
  const host = {
    ctx:{tools:{register:tool => registered.set(tool.name,tool)},get:name => name === 'attachments' ? {saveImage:async value => {saved.push(value); return attachment;}} : null},
    nativeConversations:new Map([['session',{conversation_id:'conversation'}]]),
    activeCommands:new Map([['session','command']]), requestPolicies:new Map([['session',{origin:'desktop'}]]),
    consumedCommandId:()=> 'command', commandPolicy:()=>host.requestPolicies.get('session'), checkCommandScope:async()=>{},
    authorizations:{run:async(_authorization,operation)=>operation()},
    guard:async()=>{}, actionGate:{request:async request => {approvals.push(request); return true;}},
    rpc:async()=>({commands:[{model_selection:{connection_id:'provider',model_id:'vision'}}]}),
    models:{assertImageInput:(_purpose, provider, model) => {assert.equal(provider,'provider'); assert.equal(model,'vision');}},
  };
  installDesktopObservation(host,{enableActions:true,provider:async(_host,kind)=>{observations.push(kind);return kind==='screen' ? {width:4,height:4,data:Buffer.from('jpeg').toString('base64')} : {title:'example'};}});
  return {host,registered,approvals,observations,saved,attachment};
}
const exec = () => ({agent:{id:'session'},signal:new AbortController().signal});

test('screen approval precedes capture; image is attached to the result', async()=>{
  const f=fixture(), tool=f.registered.get('mirally_desktop_screenshot');
  const result=await tool.execute({},exec());
  assert.equal(f.approvals.length,1); assert.match(f.approvals[0].detail,/会话附件/);
  assert.deepEqual(f.observations,['screen']); assert.equal(f.saved[0].mediaType,'image/jpeg');
  assert.deepEqual(tool.output.render({},result)[1],{type:'image',attachment:f.attachment});
});
test('reject, remote input, and aborted request never observe', async()=>{
  for(const mode of ['reject','remote','abort']) {
    const f=fixture(), tool=f.registered.get('mirally_desktop_screenshot');
    if(mode==='reject') f.host.actionGate.request=async()=>false;
    if(mode==='remote') f.host.requestPolicies.set('session',{origin:'mobile'});
    const e=exec(); if(mode==='abort') {const controller=new AbortController(); controller.abort(); e.signal=controller.signal;}
    await assert.rejects(tool.execute({},e)); assert.deepEqual(f.observations,[]); assert.deepEqual(f.saved,[]);
  }
});
test('Weixin observation requires approval and rechecks revoked scope before capture', async()=>{
  const f=fixture(); f.host.requestPolicies.set('session',{origin:'weixin'});
  await f.registered.get('mirally_desktop_windows').execute({},exec());
  assert.deepEqual(f.observations,['windows']);
  let revoked=false;
  f.host.checkCommandScope=async()=>{if(revoked) throw Error('revoked');};
  f.host.actionGate.request=async()=>{revoked=true;return true;};
  await assert.rejects(f.registered.get('mirally_desktop_screenshot').execute({},exec()),/revoked/);
  assert.deepEqual(f.observations,['windows']); assert.deepEqual(f.saved,[]);
  await assert.rejects(f.registered.get('mirally_desktop_click').execute({},exec()));
});
test('recheck command and origin after confirmation before observing',async()=>{
  const f=fixture(); f.host.actionGate.request=async()=>{f.host.requestPolicies.set('session',{origin:'mobile'});return true;};
  await assert.rejects(f.registered.get('mirally_desktop_foreground').execute({},exec()));
  assert.deepEqual(f.observations,[]);
});
test('non-vision model cannot request or create screenshot',async()=>{
  const f=fixture(); f.host.models.assertImageInput=()=>{throw new Error('no vision');};
  await assert.rejects(f.registered.get('mirally_desktop_screenshot').execute({},exec()),/no vision/);
  assert.equal(f.approvals.length,0);assert.deepEqual(f.observations,[]);
});
test('single click requires approval and an in-window coordinate',async()=>{
  const f=fixture(), tool=f.registered.get('mirally_desktop_click');
  const window={hwnd:123,pid:456,title:'Test',bounds:{left:0,top:0,right:100,bottom:100}};
  await assert.rejects(tool.execute({window,x:100,y:10},exec()));
  assert.equal(f.approvals.length,0);
  await tool.execute({window,x:10,y:10},exec());
  assert.match(f.approvals[0].detail,/Test/);assert.match(f.approvals[0].detail,/10, 10/);
  assert.deepEqual(f.observations,['action']);
});
test('unverified desktop input is not registered by default',()=>{
  const tools=new Map();
  installDesktopObservation({ctx:{tools:{register:tool=>tools.set(tool.name,tool)}}});
  assert.equal(tools.has('mirally_desktop_click'),false);
  assert.equal(tools.has('mirally_desktop_type'),false);
  assert.equal(tools.has('mirally_desktop_screenshot'),true);
});
