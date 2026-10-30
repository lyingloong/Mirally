/** Deliver conversation-owned artifacts through the channel's durable outbox. */
import {createCipheriv,createHash,randomBytes} from 'node:crypto';
import {validateWeixinBaseUrl,WEIXIN_PROTOCOL_REFERENCE} from './weixin-api.mjs';

const PREFIX='media-artifact:';
const LIMIT=8*1024*1024;
const CDN='https://novac2c.cdn.weixin.qq.com/c2c';
const fail=(message,code='weixin_media_invalid',deliveryState='rejected')=>Object.assign(new Error(message),{code,deliveryState});
const hash=(kind,data)=>createHash(kind).update(data).digest('hex');
const plain=value=>typeof value==='string' && value.length>0 && value.length<=512 && !/[\x00-\x1f\x7f]/.test(value);

function safeCdnUrl(raw,fixtureOrigin) {
  if(typeof raw!=='string'||raw.length>12000||/[\s\\]/.test(raw))throw fail('微信媒体上传地址无效。');
  let url;try{url=new URL(raw);}catch{throw fail('微信媒体上传地址无效。');}
  const local=fixtureOrigin && url.origin===fixtureOrigin && /^http:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?$/.test(fixtureOrigin);
  if(url.username||url.password||url.hash || !(local || url.protocol==='https:'&&!url.port
    && (url.hostname==='weixin.qq.com'||url.hostname.endsWith('.weixin.qq.com'))))throw fail('微信媒体地址不属于微信服务。');
  return url.href;
}

function kind(bytes,name) {
  if(bytes.length>=8&&bytes.subarray(0,8).equals(Buffer.from('89504e470d0a1a0a','hex'))
    || bytes.length>=3&&bytes[0]===255&&bytes[1]===216&&bytes[2]===255
    || bytes.length>=12&&bytes.toString('ascii',0,4)==='RIFF'&&bytes.toString('ascii',8,12)==='WEBP'
    || bytes.length>=6&&['GIF87a','GIF89a'].includes(bytes.toString('ascii',0,6))) return 'image';
  if(/\.(?:mp4|mov|m4v)$/i.test(name)&&bytes.length>=12&&bytes.toString('ascii',4,8)==='ftyp')return 'video';
  return 'file';
}

async function jsonBody(response) {
  if(response.redirected || response.status>=300&&response.status<400)throw fail('微信接口拒绝重定向。');
  if(!response.ok)throw fail(`微信接口 HTTP 错误（${response.status}）。`);
  const declared=response.headers.get('content-length');
  if(declared!==null&&(!/^\d+$/.test(declared)||Number(declared)>1024*1024))throw fail('微信接口响应过大。');
  const reader=response.body?.getReader();if(!reader)throw fail('微信接口响应为空。');
  const chunks=[];let count=0;
  try{for(;;){const {done,value}=await reader.read();if(done)break;count+=value.byteLength;if(count>1024*1024){await reader.cancel();throw fail('微信接口响应过大。');}chunks.push(value);}}finally{reader.releaseLock();}
  let value;try{value=JSON.parse(Buffer.concat(chunks,count).toString('utf8'));}catch{throw fail('微信接口响应无效。');}
  if(!value||typeof value!=='object'||Array.isArray(value))throw fail('微信接口响应无效。');
  const code=value.ret || value.errcode || 0;if(code)throw fail(`微信接口拒绝请求（${Number.isSafeInteger(code)?code:'未知'}）。`,'weixin_api_rejected');
  return value;
}

function headers(token) {
  const [major,minor,patch]=WEIXIN_PROTOCOL_REFERENCE.version.split('.').map(Number);
  return {'Content-Type':'application/json','iLink-App-Id':'bot','iLink-App-ClientVersion':String((major<<16)|(minor<<8)|patch),
    'AuthorizationType':'ilink_bot_token','Authorization':`Bearer ${token}`,
    'X-WECHAT-UIN':Buffer.from(String(randomBytes(4).readUInt32BE(0))).toString('base64')};
}

export class WeixinOutboundMedia {
  constructor(channel,{fetch:fetchImpl=globalThis.fetch,fixtureOrigin}={}) {this.channel=channel;this.fetch=fetchImpl;this.fixtureOrigin=fixtureOrigin;}
  isMedia(delivery) {return String(delivery?.key??'').startsWith(PREFIX);}
  async queue(event,artifact_id) {
    if(!/^[a-f0-9]{36}$/.test(artifact_id??''))throw fail('请选择当前会话的成果文件。');
    const channel=this.channel;
    const target=event.target_conversation_id ?? event.conversation_id;
    const authorize=async()=>{
      await channel.authorize(event);
      if(event.schedule_id) await channel.product.checkWeixinSchedule(event);
      else if(event.target_conversation_id) await channel.product.checkWeixinDelegation(event);
    };
    await authorize();
    const {record,data}=await channel.product.materials.read(artifact_id);
    if(record.conversation_id!==target || !plain(record.name)||!Buffer.isBuffer(data)||!data.length||data.length>LIMIT)
      throw fail('成果不属于当前会话或超过微信媒体大小上限。');
    await authorize();
    const descriptor={v:1,artifact_id,conversation_id:target,source_conversation_id:event.conversation_id,
      ...(event.schedule_id?{schedule_id:event.schedule_id}:event.target_conversation_id?{source_command_id:event.source_command_id}:{}),
      installation_id:event.installation_id,revision:event.revision,command_id:event.command_id,
      sha256:hash('sha256',data),name:record.name,bytes:data.length};
    const key=`${PREFIX}${event.command_id}:${artifact_id}`;
    const {delivery}=await channel.store('enqueue',{installation_id:event.installation_id,revision:event.revision,key,
      text:JSON.stringify(descriptor),conversation_id:target,command_id:event.command_id,context_token:event.context_token});
    return {delivery_id:delivery.delivery_id,status:delivery.status,artifact_name:record.name};
  }
  async readiness(delivery) {
    if(!this.isMedia(delivery))return 'other';
    const {commands}=await this.channel.product.rpc('command.list',{conversation_id:delivery.conversation_id,command_id:delivery.command_id});
    const state=commands?.[0]?.state;
    if(['admitted','submitted','applied'].includes(state))return 'wait';
    return state==='completed'?'ready':'cancelled';
  }
  async request(url,body,token,signal,{sending=false}={}) {
    let response,issued=false;
    try{
      issued=true;response=await this.fetch(url,{method:'POST',redirect:'manual',signal:AbortSignal.any([signal,AbortSignal.timeout(30000)]),
        headers:headers(token),body:JSON.stringify({...body,base_info:{channel_version:WEIXIN_PROTOCOL_REFERENCE.version,bot_agent:'Mirally/0.1.0'}})});
      return await jsonBody(response);
    }catch(error){
      if(error.code==='weixin_api_rejected')throw error;
      if(!sending && error.deliveryState==='rejected')throw error;
      throw fail('微信媒体请求未能确认。','weixin_media_network',sending&&issued?'unknown':'rejected');
    }
  }
  async dispatch(delivery,signal) {
    const channel=this.channel;
    if(!this.isMedia(delivery)||delivery.status!=='sending'||!delivery.state)throw fail('微信媒体投递状态无效。');
    let descriptor;try{descriptor=JSON.parse(delivery.text);}catch{throw fail('微信媒体投递记录无效。');}
    if(!descriptor||descriptor.v!==1||!/^[a-f0-9]{36}$/.test(descriptor.artifact_id??'')||!/^[a-f0-9]{64}$/.test(descriptor.sha256??'')
      ||descriptor.conversation_id!==delivery.conversation_id||descriptor.installation_id!==delivery.installation_id
      ||descriptor.revision!==delivery.revision||descriptor.command_id!==delivery.command_id||!plain(descriptor.source_conversation_id)
      ||!plain(descriptor.name)||!Number.isSafeInteger(descriptor.bytes)||descriptor.bytes<1||descriptor.bytes>LIMIT)
      throw fail('微信媒体投递记录无效。');
    const authorize=async()=>{
      const state=await channel.authorize({...delivery,conversation_id:descriptor.source_conversation_id});
      if(state.installation_id!==delivery.installation_id||state.revision!==delivery.revision)throw fail('微信绑定已改变。','weixin_scope_revoked');
      if(descriptor.schedule_id) {
        if(!plain(descriptor.schedule_id))throw fail('微信定时任务记录无效。','weixin_scope_revoked');
        try { await channel.product.checkWeixinSchedule({installation_id:delivery.installation_id,revision:delivery.revision,
          conversation_id:descriptor.source_conversation_id,target_conversation_id:descriptor.conversation_id,
          command_id:delivery.command_id,schedule_id:descriptor.schedule_id}); }
        catch { throw fail('微信定时任务授权已失效。','weixin_scope_revoked'); }
      } else if(descriptor.conversation_id!==descriptor.source_conversation_id) {
        if(!plain(descriptor.source_command_id))throw fail('微信委托记录无效。','weixin_scope_revoked');
        try { await channel.product.checkWeixinDelegation({installation_id:delivery.installation_id,revision:delivery.revision,
          conversation_id:descriptor.source_conversation_id,source_command_id:descriptor.source_command_id,
          target_conversation_id:descriptor.conversation_id,command_id:delivery.command_id},true); }
        catch { throw fail('微信委托已失效。','weixin_scope_revoked'); }
      }
      if(!await channel.product.canDeliverWeixin(delivery))throw fail('原会话或发送授权已失效。','weixin_scope_revoked');
      return state;
    };
    let state=await authorize();
    const {record,data}=await channel.product.materials.read(descriptor.artifact_id);
    if(record.conversation_id!==descriptor.conversation_id||record.name!==descriptor.name||!Buffer.isBuffer(data)
      ||data.length!==descriptor.bytes||hash('sha256',data)!==descriptor.sha256)throw fail('成果内容已变化，请重新请求发送。','weixin_media_changed');
    state=await authorize();signal.throwIfAborted();
    const mediaKind=kind(data,record.name),mediaType=mediaKind==='image'?1:mediaKind==='video'?2:3;
    const aes=randomBytes(16),filekey=randomBytes(16).toString('hex'),cipher=createCipheriv('aes-128-ecb',aes,null);
    const encrypted=Buffer.concat([cipher.update(data),cipher.final()]);
    const base=validateWeixinBaseUrl(state.base_url,{allowLoopback:channel.allowLoopback===true});
    const upload=await this.request(`${base}/ilink/bot/getuploadurl`,{filekey,media_type:mediaType,to_user_id:state.peer_id,
      rawsize:data.length,rawfilemd5:hash('md5',data),filesize:encrypted.length,no_need_thumb:true,aeskey:aes.toString('hex')},state.token,signal);
    await authorize();signal.throwIfAborted();
    let raw=upload.upload_full_url;
    if(!raw) {
      if(typeof upload.upload_param!=='string'||!upload.upload_param||upload.upload_param.length>8192)throw fail('微信没有返回有效上传地址。');
      const baseCdn=this.fixtureOrigin?`${this.fixtureOrigin}/c2c`:CDN;const target=new URL(`${baseCdn}/upload`);
      target.searchParams.set('encrypted_query_param',upload.upload_param);target.searchParams.set('filekey',filekey);raw=target.href;
    }
    const url=safeCdnUrl(raw,this.fixtureOrigin);let uploaded;
    try{uploaded=await this.fetch(url,{method:'POST',redirect:'manual',headers:{'Content-Type':'application/octet-stream'},body:encrypted,
      signal:AbortSignal.any([signal,AbortSignal.timeout(30000)])});}catch{throw fail('微信媒体上传失败。','weixin_media_upload');}
    if(uploaded.redirected||uploaded.status!==200)throw fail(`微信媒体上传失败（${uploaded.status}）。`,'weixin_media_upload');
    const param=uploaded.headers.get('x-encrypted-param');
    if(typeof param!=='string'||!param||param.length>8192||/[\x00-\x1f\x7f]/.test(param))throw fail('微信媒体上传回执无效。','weixin_media_upload');
    await authorize();signal.throwIfAborted();
    const media={encrypt_query_param:param,aes_key:Buffer.from(aes.toString('hex')).toString('base64'),encrypt_type:1};
    const item=mediaKind==='image'?{type:2,image_item:{media,mid_size:encrypted.length}}
      :mediaKind==='video'?{type:5,video_item:{media,video_size:encrypted.length}}
      :{type:4,file_item:{media,file_name:record.name,len:String(data.length)}};
    const result=await this.request(`${base}/ilink/bot/sendmessage`,{msg:{from_user_id:'',to_user_id:state.peer_id,client_id:delivery.delivery_id,
      message_type:2,message_state:2,context_token:delivery.context_token||state.context_token,item_list:[item]}},state.token,signal,{sending:true});
    if(result.ret!==0 && !result.message_id)throw fail('微信媒体发送回执无效。','weixin_media_response','unknown');
    return {accepted:true,client_id:delivery.delivery_id,kind:mediaKind};
  }
}
