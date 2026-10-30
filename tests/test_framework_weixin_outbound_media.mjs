import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createDecipheriv} from 'node:crypto';
import {WeixinOutboundMedia} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin-media-outbound.mjs';

const artifactId='abcdef0123456789abcdef0123456789abcd';
const event={installation_id:'bound',revision:3,conversation_id:'conversation',command_id:'weixin-command',context_token:'context'};
const original=Buffer.from('小报告\n', 'utf8');
async function fixture(t,{image=false,owner='conversation'}={}) {
  let upload,wire,failSend=false;
  const server=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);const data=Buffer.concat(chunks);
    if(req.url==='/ilink/bot/getuploadurl') {upload=JSON.parse(data);res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({ret:0,upload_param:'fixture-upload'}));return;}
    if(req.url.startsWith('/c2c/upload?')) {upload.encrypted=data;res.writeHead(200,{'x-encrypted-param':'download-fixture'});res.end();return;}
    if(req.url==='/ilink/bot/sendmessage') {
      wire=JSON.parse(data);if(failSend){req.socket.destroy();return;}
      res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({ret:0,message_id:'1'}));return;
    }
    res.writeHead(404);res.end();
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>{server.closeAllConnections();server.close();});
  const origin=`http://127.0.0.1:${server.address().port}`;
  const state={...event,base_url:origin,peer_id:'owner',token:'secret',context_token:'context'},outbox=new Map();
  let bytes=image?Buffer.from('89504e470d0a1a0a0000000d49484452','hex'):original,commandState='applied',allowed=true;
  const checks=[];
  const channel={allowLoopback:true,product:{materials:{read:async id=>{assert.equal(id,artifactId);return {record:{id,name:image?'image.png':'报告.txt',conversation_id:owner},data:bytes};}},
    rpc:async method=>method==='settings.get'?{weixin_remote_control_enabled:allowed}:{commands:[{state:commandState}]},canDeliverWeixin:async()=>allowed,
    checkWeixinDelegation:async(scope,delivery)=>{checks.push({scope,delivery});if(!allowed)throw Error('revoked');}},
    authorize:async scope=>{if(!allowed||scope.installation_id!=='bound'||scope.revision!==3||scope.conversation_id!=='conversation')throw Error('revoked');return state;},
    store:async(method,params)=>{assert.equal(method,'enqueue');let item=outbox.get(params.key);if(!item){item={...params,delivery_id:'delivery-1',status:'pending'};outbox.set(params.key,item);}else assert.equal(item.text,params.text);return {delivery:item};}};
  const transport=new WeixinOutboundMedia(channel,{fixtureOrigin:origin});
  return {transport,channel,outbox,state,checks,get upload(){return upload;},get wire(){return wire;},
    changeBytes(value){bytes=value;},changeCommandState(value){commandState=value;},revoke(){allowed=false;},failSend(){failSend=true;}};
}

test('user-requested file is durably queued and sent only after original command completion',async t=>{
  const f=await fixture(t);const receipt=await f.transport.queue(event,artifactId);
  assert.equal(receipt.status,'pending');assert.equal(f.outbox.size,1);
  const queued=[...f.outbox.values()][0];assert.equal(await f.transport.readiness(queued),'wait');
  f.changeCommandState('completed');assert.equal(await f.transport.readiness(queued),'ready');
  const delivery={...queued,status:'sending',state:f.state};
  const result=await f.transport.dispatch(delivery,new AbortController().signal);
  assert.deepEqual(result,{accepted:true,client_id:'delivery-1',kind:'file'});
  assert.equal(f.upload.media_type,3);assert.equal(f.upload.rawsize,original.length);
  const media=f.wire.msg.item_list[0];assert.equal(media.type,4);assert.equal(media.file_item.file_name,'报告.txt');
  assert.equal(f.wire.msg.client_id,'delivery-1');assert.equal(media.file_item.len,String(original.length));
  const key=Buffer.from(Buffer.from(media.file_item.media.aes_key,'base64').toString('ascii'),'hex');
  const decoder=createDecipheriv('aes-128-ecb',key,null);assert.deepEqual(Buffer.concat([decoder.update(f.upload.encrypted),decoder.final()]),original);
  assert.equal((await f.transport.queue(event,artifactId)).delivery_id,receipt.delivery_id);assert.equal(f.outbox.size,1);
});

test('image uses an image message and MIME is determined from bytes',async t=>{
  const f=await fixture(t,{image:true});await f.transport.queue(event,artifactId);f.changeCommandState('completed');
  const delivery={...[...f.outbox.values()][0],status:'sending',state:f.state};
  const result=await f.transport.dispatch(delivery,new AbortController().signal);
  assert.equal(result.kind,'image');assert.equal(f.upload.media_type,1);assert.equal(f.wire.msg.item_list[0].type,2);
});

test('changed artifact, revoked binding, and unknown send never report success',async t=>{
  const f=await fixture(t);await f.transport.queue(event,artifactId);f.changeCommandState('completed');
  const delivery={...[...f.outbox.values()][0],status:'sending',state:f.state};
  f.changeBytes(Buffer.from('different'));await assert.rejects(f.transport.dispatch(delivery,new AbortController().signal),/已变化/);assert.equal(f.wire,undefined);
  f.changeBytes(original);f.revoke();await assert.rejects(f.transport.dispatch(delivery,new AbortController().signal),/revoked/);assert.equal(f.wire,undefined);
  const g=await fixture(t);await g.transport.queue(event,artifactId);g.changeCommandState('completed');g.failSend();
  const uncertain={...[...g.outbox.values()][0],status:'sending',state:g.state};
  await assert.rejects(g.transport.dispatch(uncertain,new AbortController().signal),error=>error.deliveryState==='unknown');
});

test('cross-conversation artifacts cannot be queued',async t=>{
  const f=await fixture(t,{owner:'other'});await assert.rejects(f.transport.queue(event,artifactId),/不属于当前会话/);assert.equal(f.outbox.size,0);
});

test('delegated artifact preserves source authorization and target ownership through durable delivery',async t=>{
  const f=await fixture(t,{owner:'target'});
  const scope={...event,source_command_id:'source-command',target_conversation_id:'target',command_id:'target-command'};
  await f.transport.queue(scope,artifactId);const queued=[...f.outbox.values()][0];
  assert.equal(queued.conversation_id,'target');assert.equal(queued.command_id,'target-command');
  const descriptor=JSON.parse(queued.text);assert.equal(descriptor.source_conversation_id,'conversation');assert.equal(descriptor.conversation_id,'target');
  f.changeCommandState('completed');const receipt=await f.transport.dispatch({...queued,status:'sending',state:f.state},new AbortController().signal);
  assert.equal(receipt.accepted,true);assert.ok(f.checks.some(c=>c.delivery===true&&c.scope.target_conversation_id==='target'&&c.scope.source_command_id==='source-command'));
  const other=await fixture(t,{owner:'different'});await assert.rejects(other.transport.queue(scope,artifactId),/不属于当前会话/);assert.equal(other.outbox.size,0);
});
