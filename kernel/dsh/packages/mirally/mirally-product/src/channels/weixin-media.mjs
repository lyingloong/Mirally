/** Bounded WeChat media decoding. Transport is independent of the Agent loop. */
import {createDecipheriv, createHash} from 'node:crypto';

const LIMIT = 8 * 1024 * 1024;
const CDN = 'https://novac2c.cdn.weixin.qq.com/c2c';
const fail = (message, code='weixin_media_invalid') => Object.assign(new Error(message),{code});
const text = value => typeof value==='string' ? value : '';
const stable = value => Array.isArray(value) ? value.map(stable) : value && typeof value==='object'
  ? Object.fromEntries(Object.keys(value).sort().map(key=>[key,stable(value[key])])) : value;

/** Do not persist CDN keys or URLs. This digest protects the full ordered wire message. */
export function inspectWeixinMedia(items) {
  if (!Array.isArray(items) || !items.length || items.length>12) throw fail('一条消息最多接收 12 项内容。');
  const encoded=JSON.stringify(stable(items));
  if (Buffer.byteLength(encoded)>128000) throw fail('微信消息描述过长。');
  for (const item of items) {
    if (!item || typeof item!=='object' || ![1,2,3,4,5].includes(item.type)) throw fail('这条微信消息含暂不支持的内容。');
    if (item.type===1 && typeof item.text_item?.text!=='string') throw fail('微信文字内容无效。');
    if (item.type===2 && !item.image_item || item.type===3 && !item.voice_item
      || item.type===4 && !item.file_item || item.type===5 && !item.video_item) throw fail('微信媒体描述无效。');
  }
  const message=items.filter(item=>item.type===1).map(item=>item.text_item.text).join('\n').trim();
  if(message.length>100000) throw fail('微信文字过长。');
  return {text:message || '微信媒体消息', content_identity:createHash('sha256').update(encoded).digest('hex'),
    hasMedia:items.some(item=>item.type!==1)};
}

function mediaUrl(media, {fixtureOrigin}={}) {
  let raw=text(media?.full_url);
  if(!raw) {
    const query=text(media?.encrypt_query_param);
    if(!query || query.length>8192) throw fail('微信媒体下载地址不可用。');
    const base=fixtureOrigin ? `${fixtureOrigin}/c2c` : CDN;
    const url=new URL(`${base}/download`);url.searchParams.set('encrypted_query_param',query);raw=url.href;
  }
  if(raw.length>12000 || /[\s\\]/.test(raw)) throw fail('微信媒体地址无效。');
  let url;try{url=new URL(raw);}catch{throw fail('微信媒体地址无效。');}
  const local = fixtureOrigin && url.origin===fixtureOrigin && /^http:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?$/.test(fixtureOrigin);
  if(url.username||url.password||url.hash || !(local || url.protocol==='https:' && !url.port
    && (url.hostname==='weixin.qq.com'||url.hostname.endsWith('.weixin.qq.com'))))
    throw fail('微信媒体地址不属于微信服务。');
  return url.href;
}

function keyFor(item) {
  const object=item.type===2?item.image_item:item.type===3?item.voice_item:item.type===4?item.file_item:item.video_item;
  if(item.type===2 && object.aeskey!==undefined) {
    if(!/^[a-f\d]{32}$/i.test(object.aeskey)) throw fail('微信图片密钥无效。');
    return Buffer.from(object.aeskey,'hex');
  }
  const raw=object.media?.aes_key;
  if(!raw && item.type===2) return null;
  if(typeof raw!=='string'||raw.length>128||!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(raw)) throw fail('微信媒体密钥不可用。');
  const decoded=Buffer.from(raw,'base64');
  if(decoded.length===16)return decoded;
  if(decoded.length===32&&/^[a-f\d]{32}$/i.test(decoded.toString('ascii')))return Buffer.from(decoded.toString('ascii'),'hex');
  throw fail('微信媒体密钥无效。');
}

async function readBounded(response, limit) {
  if(response.redirected || response.status>=300&&response.status<400) throw fail('微信媒体服务拒绝重定向。');
  if(!response.ok) throw fail(`微信媒体下载失败（${response.status}）。`,'weixin_media_download');
  const size=response.headers.get('content-length');
  if(size!==null && (!/^\d+$/.test(size)||Number(size)>limit)) throw fail('微信媒体超过大小上限。');
  const reader=response.body?.getReader();if(!reader) throw fail('微信媒体内容为空。');
  const chunks=[];let count=0;
  try {for(;;){const {done,value}=await reader.read();if(done)break;count+=value.byteLength;if(count>limit){await reader.cancel();throw fail('微信媒体超过大小上限。');}chunks.push(value);}}
  finally{reader.releaseLock();}
  if(!count)throw fail('微信媒体内容为空。');
  return Buffer.concat(chunks,count);
}

function imageMime(data) {
  if(data.length>=8&&data.subarray(0,8).equals(Buffer.from('89504e470d0a1a0a','hex')))return ['image/png','png'];
  if(data.length>=3&&data[0]===255&&data[1]===216&&data[2]===255)return ['image/jpeg','jpg'];
  if(data.length>=12&&data.toString('ascii',0,4)==='RIFF'&&data.toString('ascii',8,12)==='WEBP')return ['image/webp','webp'];
  if(data.length>=6&&['GIF87a','GIF89a'].includes(data.toString('ascii',0,6)))return ['image/gif','gif'];
  throw fail('图片格式暂不支持。');
}

function pcmWav(pcm,rate) {
  if(!Buffer.isBuffer(pcm)||!pcm.length||pcm.length>LIMIT||pcm.length%2||!Number.isSafeInteger(rate)||rate<8000||rate>48000) throw fail('语音格式无效。');
  const out=Buffer.alloc(44+pcm.length);out.write('RIFF',0);out.writeUInt32LE(out.length-8,4);out.write('WAVEfmt ',8);
  out.writeUInt32LE(16,16);out.writeUInt16LE(1,20);out.writeUInt16LE(1,22);out.writeUInt32LE(rate,24);
  out.writeUInt32LE(rate*2,28);out.writeUInt16LE(2,32);out.writeUInt16LE(16,34);out.write('data',36);out.writeUInt32LE(pcm.length,40);pcm.copy(out,44);return out;
}

async function audioForRecognition(bytes,voice) {
  if(bytes.length>=12&&bytes.toString('ascii',0,4)==='RIFF'&&bytes.toString('ascii',8,12)==='WAVE')return {data:bytes,media_type:'audio/wav',name:'weixin.wav'};
  if(voice.encode_type===6 || bytes.subarray(0,10).toString('ascii').includes('#!SILK')) {
    let decoded;
    try {const silk=await import('silk-wasm');decoded=await silk.decode(bytes,24000);}
    catch {throw fail('微信语音解码失败，请改用文字或重新发送。','weixin_voice_decode');}
    return {data:pcmWav(Buffer.from(decoded.data),24000),media_type:'audio/wav',name:'weixin.wav'};
  }
  if(voice.encode_type===1 && voice.bits_per_sample===16) return {data:pcmWav(bytes,voice.sample_rate),media_type:'audio/wav',name:'weixin.wav'};
  if(voice.encode_type===7) return {data:bytes,media_type:'audio/mpeg',name:'weixin.mp3'};
  if(voice.encode_type===8) return {data:bytes,media_type:'audio/ogg',name:'weixin.ogg'};
  throw fail('暂不支持此微信语音编码，请改用文字或其他语音格式。','weixin_voice_unsupported');
}

export class WeixinInboundMedia {
  constructor(product,{fetch:fetchImpl=globalThis.fetch,fixtureOrigin}={}) {this.product=product;this.fetch=fetchImpl;this.fixtureOrigin=fixtureOrigin;}
  async download(item,signal) {
    const object=item.type===2?item.image_item:item.type===3?item.voice_item:item.type===4?item.file_item:item.video_item;
    const url=mediaUrl(object.media,{fixtureOrigin:this.fixtureOrigin});const key=keyFor(item);
    let response;
    try {response=await this.fetch(url,{method:'GET',redirect:'manual',signal:AbortSignal.any([signal,AbortSignal.timeout(30000)])});}
    catch {throw fail('微信媒体下载失败，请稍后重试。','weixin_media_download');}
    const encrypted=await readBounded(response,LIMIT+16);let bytes=encrypted;
    if(key) {
      try {const decipher=createDecipheriv('aes-128-ecb',key,null);bytes=Buffer.concat([decipher.update(encrypted),decipher.final()]);}
      catch {throw fail('微信媒体解密失败，请重新发送。','weixin_media_decrypt');}
    }
    if(!bytes.length||bytes.length>LIMIT)throw fail('微信媒体超过 8 MiB 上限。');
    if(item.type===4&&object.len!==undefined && (!/^\d+$/.test(String(object.len)) || Number(object.len)!==bytes.length))throw fail('微信文件长度不匹配，请重新发送。');
    if(item.type===4&&object.md5!==undefined && (!/^[a-f\d]{32}$/i.test(object.md5)||createHash('md5').update(bytes).digest('hex')!==object.md5.toLowerCase()))throw fail('微信文件校验失败，请重新发送。');
    return bytes;
  }
  async prepare(event,items,{authorize,signal}={}) {
    if(typeof authorize!=='function'||!signal)throw fail('媒体处理缺少授权上下文。');
    inspectWeixinMedia(items);const lines=[],materials=[];
    for(let i=0;i<items.length;i++) {
      const item=items[i];signal.throwIfAborted();await authorize(event);
      if(item.type===1){if(item.text_item.text.trim())lines.push(item.text_item.text.trim());continue;}
      if(item.type===3) {
        const voice=item.voice_item;let transcript=text(voice.text).trim();
        if(!transcript) {
          const bytes=await this.download(item,signal);await authorize(event);
          const audio=await audioForRecognition(bytes,voice);signal.throwIfAborted();await authorize(event);
          if(!this.product.speech)throw fail('尚未设置语音识别，请在电脑设置中配置。','speech_unavailable');
          const request_id=createHash('sha256').update(`${event.command_id}:voice:${i}`).digest('hex');
          const result=await this.product.speech.transcribe({...audio,request_id,signal});transcript=text(result?.text).trim();
          if(!transcript)throw fail('语音未识别出文字，请重新发送。','speech_empty');
        }
        if(transcript.length>24000)throw fail('语音转写内容过长。');
        lines.push(`微信语音转写：${transcript}`);continue;
      }
      const bytes=await this.download(item,signal);await authorize(event);let name,type;
      if(item.type===2){const [mime,extension]=imageMime(bytes);type=mime;name=`微信图片-${i+1}.${extension}`;}
      else if(item.type===4){name=text(item.file_item.file_name) || `微信文件-${i+1}`;type='application/octet-stream';}
      else {name=`微信视频-${i+1}.mp4`;type='video/mp4';}
      const material=await this.product.materials.upload({conversation_id:event.conversation_id,name,media_type:type,
        data:bytes.toString('base64'),request_id:`${event.command_id}:media:${i}`});
      await authorize(event);materials.push(material.id);lines.push(`微信附件 ${materials.length}：${material.name}${item.type===5?'（视频已保存，尚未解析）':''}`);
    }
    const message=lines.join('\n').trim();if(!message)throw fail('微信消息没有可处理的内容。');
    return {text:message,materials};
  }
}
