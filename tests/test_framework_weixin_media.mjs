import test from 'node:test';
import assert from 'node:assert/strict';
import {createCipheriv,randomBytes} from 'node:crypto';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {inspectWeixinMedia,WeixinInboundMedia} from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin-media.mjs';

const signal=()=>new AbortController().signal;
const event={conversation_id:'conversation-a',command_id:'weixin-event-1'};
const encrypt=(bytes,key)=>{const cipher=createCipheriv('aes-128-ecb',key,null);return Buffer.concat([cipher.update(bytes),cipher.final()]);};
const image=Buffer.from('89504e470d0a1a0a0000000d49484452','hex');
const dataKey=key=>Buffer.from(key.toString('hex')).toString('base64');
const productRequire=createRequire(new URL('../kernel/dsh/packages/mirally/mirally-product/package.json',import.meta.url));

async function serverFixture(t, entries) {
  const server=createServer((req,res)=>{const key=new URL(req.url,'http://localhost').searchParams.get('encrypted_query_param');
    if(!entries[key]){res.writeHead(404);res.end();return;}res.writeHead(200,{'content-type':'application/octet-stream'});res.end(entries[key]);});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>{server.closeAllConnections();server.close();});
  return `http://127.0.0.1:${server.address().port}`;
}

test('ordered content identity includes media references, text and filenames',()=>{
  const one=[{type:1,text_item:{text:'看看'}},{type:4,file_item:{file_name:'a.txt',media:{encrypt_query_param:'x',aes_key:'secret'}}}];
  const a=inspectWeixinMedia(one);assert.equal(a.text,'看看');assert.match(a.content_identity,/^[a-f0-9]{64}$/);assert.equal(a.hasMedia,true);
  assert.notEqual(a.content_identity,inspectWeixinMedia([{...one[0]},{type:4,file_item:{...one[1].file_item,file_name:'b.txt'}}]).content_identity);
  assert.notEqual(a.content_identity,inspectWeixinMedia([...one].reverse()).content_identity);
  assert.throws(()=>inspectWeixinMedia([{type:9}]),/不支持/);
});

test('text, image and file become the original conversation materials with verified bytes',async t=>{
  const k1=randomBytes(16),k2=randomBytes(16),file=Buffer.from('测试文件','utf8');
  const origin=await serverFixture(t,{one:encrypt(image,k1),two:encrypt(file,k2)}),uploads=[];
  const adapter=new WeixinInboundMedia({materials:{upload:async request=>{uploads.push(request);return {id:`id-${uploads.length}`,name:request.name};}}},{fixtureOrigin:origin});
  const prepared=await adapter.prepare(event,[{type:1,text_item:{text:'看看图片和附件'}},
    {type:2,image_item:{aeskey:k1.toString('hex'),media:{encrypt_query_param:'one'}}},
    {type:4,file_item:{file_name:'报告.txt',len:String(file.length),media:{encrypt_query_param:'two',aes_key:dataKey(k2)}}}],{authorize:async()=>{},signal:signal()});
  assert.deepEqual(prepared.materials,['id-1','id-2']);assert.match(prepared.text,/看看图片和附件/);
  assert.ok(uploads.every(item=>item.conversation_id===event.conversation_id));assert.deepEqual(uploads.map(item=>item.request_id),['weixin-event-1:media:1','weixin-event-1:media:2']);
  assert.equal(uploads[0].media_type,'image/png');assert.deepEqual(Buffer.from(uploads[1].data,'base64'),file);
});

test('silk voice is decoded as WAV and sent to the shared speech provider',async t=>{
  const pcm=Buffer.alloc(24000*2);for(let i=0;i<24000;i++)pcm.writeInt16LE(Math.round(Math.sin(i/12)*2000),i*2);
  const {encode}=await import(pathToFileURL(productRequire.resolve('silk-wasm')).href);
  const silk=Buffer.from((await encode(pcm,24000)).data),key=randomBytes(16);
  const origin=await serverFixture(t,{voice:encrypt(silk,key)}),calls=[];
  const adapter=new WeixinInboundMedia({speech:{transcribe:async audio=>{calls.push(audio);return {text:'测试语音'};}}},{fixtureOrigin:origin});
  const result=await adapter.prepare(event,[{type:3,voice_item:{encode_type:6,media:{encrypt_query_param:'voice',aes_key:dataKey(key)}}}],{authorize:async()=>{},signal:signal()});
  assert.match(result.text,/微信语音转写：测试语音/);assert.equal(calls[0].media_type,'audio/wav');assert.equal(calls[0].data.toString('ascii',0,4),'RIFF');
});

test('platform transcript can be used without fetching or a configured recognizer',async()=>{
  const adapter=new WeixinInboundMedia({},{fetch:async()=>{throw Error('must not fetch');}});
  const result=await adapter.prepare(event,[{type:3,voice_item:{text:'平台识别内容'}}],{authorize:async()=>{},signal:signal()});
  assert.equal(result.text,'微信语音转写：平台识别内容');assert.deepEqual(result.materials,[]);
});

test('unsafe URLs, wrong keys, tampered lengths and revoked routes are rejected',async t=>{
  const key=randomBytes(16),origin=await serverFixture(t,{one:encrypt(image,key),bad:encrypt(Buffer.from('text'),key)});
  let uploads=0,checks=0;
  const adapter=new WeixinInboundMedia({materials:{upload:async()=>{uploads++;return {id:'id',name:'n'};}}},{fixtureOrigin:origin});
  await assert.rejects(adapter.download({type:2,image_item:{media:{full_url:'http://169.254.169.254/latest/meta-data'}}},signal()),/不属于微信/);
  await assert.rejects(adapter.download({type:4,file_item:{len:'999',media:{encrypt_query_param:'bad',aes_key:dataKey(key)}}},signal()),/长度不匹配/);
  await assert.rejects(adapter.download({type:2,image_item:{aeskey:'z',media:{encrypt_query_param:'one'}}},signal()),/密钥/);
  await assert.rejects(adapter.prepare(event,[{type:2,image_item:{aeskey:key.toString('hex'),media:{encrypt_query_param:'one'}}}],{authorize:async()=>{if(++checks===2)throw Error('revoked');},signal:signal()}),/revoked/);
  assert.equal(uploads,0);
});
