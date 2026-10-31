/** Paired mobile surface. Never exposes the desktop control plane or host token. */
import {createServer} from 'node:http';
import {createHash, randomBytes, randomUUID, timingSafeEqual} from 'node:crypto';
import {existsSync, mkdirSync, readFileSync, writeFileSync, renameSync, realpathSync} from 'node:fs';
import {readFile} from 'node:fs/promises';
import {resolve, join, sep, extname} from 'node:path';

const TTL = 5 * 60_000, DEVICE_TTL = 30 * 86400_000;
const DEFAULTS = Object.freeze({home:'work',navigation:['work','conversations','inbox','settings'],hidden:[],
  work_sort:'attention',show_completed:true,conversation_sort:'updated'});
const UI_KEYS = new Set(Object.keys(DEFAULTS));
const MIME = {'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8',
  '.svg':'image/svg+xml','.png':'image/png','.ico':'image/x-icon','.woff2':'font/woff2','.woff':'font/woff'};
const hash = value => createHash('sha256').update(String(value)).digest('hex');
const secretEqual = (a,b) => typeof a === 'string' && typeof b === 'string' && timingSafeEqual(Buffer.from(hash(a)),Buffer.from(hash(b)));
const fault = (message,code='invalid_request',status=400) => Object.assign(new Error(message),{code,status});
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const fresh = () => ({version:1,revision:0,enabled:true,port:0,external_url:'',devices:[],user_preferences:{},device_preferences:{},pending:null});
function text(value,label,max=512) {
  if (typeof value !== 'string' || !value.trim() || value.length>max) throw fault(`${label}无效。`);
  return value.trim();
}
function integer(value,label,low,high) {
  if(!Number.isSafeInteger(value) || value<low || value>high) throw fault(`${label}无效。`);
  return value;
}
function preferences(value) {
  if(!isObject(value) || Object.keys(value).some(key=>!UI_KEYS.has(key))) throw fault('不支持的手机界面设置。');
  const result={};
  for(const [key,item] of Object.entries(value)) {
    if(key==='home' && ['work','conversations','last'].includes(item) || key==='work_sort' && ['attention','updated'].includes(item)
      || key==='conversation_sort' && ['updated','pinned'].includes(item) || key==='show_completed' && typeof item==='boolean') result[key]=item;
    else if(key==='navigation' && Array.isArray(item) && item.length===4 && new Set(item).size===4 && item.every(x=>DEFAULTS.navigation.includes(x))) result[key]=[...item];
    else if(key==='hidden' && Array.isArray(item) && item.length<=3 && item.every(x=>['work','conversations','inbox'].includes(x))) result[key]=[...new Set(item)];
    else throw fault('手机界面设置的值无效。');
  }
  return result;
}
export function mobileExternalUrl(value) {
  if(value === '') return '';
  if(typeof value!=='string' || value.length>2048) throw fault('请输入私人 HTTPS 访问地址。');
  let url;try{url=new URL(value);}catch{throw fault('请输入完整的私人 HTTPS 访问地址。');}
  if(url.protocol!=='https:' || url.username || url.password || url.search || url.hash || !['','/','/mobile','/mobile/'].includes(url.pathname))
    throw fault('访问地址必须是 HTTPS，不含凭据、查询参数或额外路径。');
  return url.origin+'/mobile';
}
async function input(request) {
  const chunks=[];let size=0;
  for await(const chunk of request) {size+=chunk.length;if(size>1024*1024) throw fault('请求超过 1 MiB。','request_too_large',413);chunks.push(chunk);}
  let value;try{value=JSON.parse(Buffer.concat(chunks).toString('utf8')||'{}');}catch{throw fault('请求必须是有效 JSON。');}
  if(!isObject(value)) throw fault('请求必须是 JSON 对象。');
  return value;
}
function reply(response,status,value) {
  response.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});
  response.end(JSON.stringify(value));
}
function security(response) {
  response.setHeader('Cache-Control','no-store');response.setHeader('X-Content-Type-Options','nosniff');
  response.setHeader('Referrer-Policy','no-referrer');response.setHeader('X-Frame-Options','DENY');
  response.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
}

/** A single product owner, optional private HTTPS proxy, independently revocable phones. */
export class MobileAccess {
  constructor(product,{now=Date.now}={}) {
    this.product=product;this.now=now;this.path=join(product.home,'mobile-access.json');this.state=fresh();
    this.principals=new WeakSet();this.attempts=new Map();this.pendingTasks=new Set();this.queue=Promise.resolve();this.closed=false;
  }
  async start() {
    try {
      if(existsSync(this.path)) {
        if(readFileSync(this.path).length>256*1024) throw new Error('文件过大');
        const state=JSON.parse(readFileSync(this.path,'utf8'));
        if(state.version!==1 || !Number.isSafeInteger(state.revision) || !Array.isArray(state.devices) || state.devices.length>40
          || typeof state.enabled!=='boolean' || !isObject(state.device_preferences)) throw new Error('版本或结构无效');
        state.user_preferences=preferences(state.user_preferences ?? {});
        for(const [id,value] of Object.entries(state.device_preferences)) state.device_preferences[id]=preferences(value);
        state.external_url=mobileExternalUrl(state.external_url);
        state.port=integer(state.port??0,'手机端口',0,65535);
        for(const d of state.devices) {
          if(!isObject(d) || typeof d.id!=='string' || !/^[a-f0-9]{64}$/.test(d.token_hash??'')
            || !Number.isSafeInteger(d.revision) || !Number.isSafeInteger(d.expires_at)) throw new Error('设备结构无效');
        }
        this.state=state;
      }
      if(this.state.enabled) await this.listen();
    } catch(error) {
      await this.closeListener();
      this.error='手机连接配置无法读取，已仅停用手机入口；保留原文件以便恢复。';
      this.state=fresh();this.state.enabled=false;
    }
  }
  persist() {
    if(this.error) throw fault(this.error,'mobile_configuration_invalid',409);
    mkdirSync(this.product.home,{recursive:true});
    const tmp=this.path+'.tmp-'+randomUUID();
    writeFileSync(tmp,JSON.stringify(this.state),{mode:0o600});renameSync(tmp,this.path);
  }
  serial(operation) {
    const next=this.queue.then(()=>{if(this.closed)throw fault('手机入口已关闭。','not_ready',503);return operation();});
    this.queue=next.catch(()=>{});return next;
  }
  async listen() {
    if(this.server) return;
    const server=createServer((request,response)=>{
      const task=this.handle(request,response).catch(error=>{
        if(!response.headersSent) reply(response,error.status??(error.code==='CONFLICT'?409:400),{error:{code:error.code??'mobile_error',message:String(error.message??'请求失败').slice(0,1000)}});
        else response.end();
      });
      this.pendingTasks.add(task);task.finally(()=>this.pendingTasks.delete(task));
    });
    server.requestTimeout=30_000;server.headersTimeout=15_000;server.maxHeadersCount=40;
    await new Promise((resolveReady,reject)=>{server.once('error',reject);server.listen(this.state.port??0,'127.0.0.1',resolveReady);});
    this.server=server;this.url=`http://127.0.0.1:${server.address().port}/mobile`;
    if(this.state.port!==server.address().port){this.state.port=server.address().port;this.persist();}
  }
  async closeListener() {
    const server=this.server;this.server=null;this.url=null;
    if(!server)return;
    await new Promise(resolveClosed=>{server.close(resolveClosed);server.closeIdleConnections?.();server.closeAllConnections?.();});
  }
  async stop() {
    this.closed=true;
    await this.closeListener();await this.queue;await Promise.allSettled([...this.pendingTasks]);
  }
  publicDevice(device) {
    return {id:device.id,name:device.name,revision:device.revision,created_at:device.created_at,last_seen:device.last_seen,
      expires_at:device.expires_at,revoked:device.revoked===true,expired:device.expires_at<=this.now()};
  }
  status() {
    return {enabled:this.state.enabled,ready:!!this.server,url:this.url,local_url:this.url,external_url:this.state.external_url,
      revision:this.state.revision,devices:this.state.devices.map(d=>this.publicDevice(d)),...(this.error?{error:this.error}:{}),
      connection_hint:this.state.external_url?'通过你配置的私人 HTTPS 转发访问，电脑需在线。':'当前仅本机可访问。手机异地访问需先配置私人 HTTPS 转发；本机地址不能直接在手机使用。',
      proxy_target:this.url?new URL(this.url).origin:null,
      tailscale_command:this.url?`tailscale serve --bg ${new URL(this.url).origin}`:null};
  }
  management(method,params={}) {
    if(method==='mobile.status')return Promise.resolve(this.status());
    return this.serial(async()=>{
      if(this.error)throw fault(this.error,'mobile_configuration_invalid',409);
      if(method==='mobile.configure') {
        if(Object.keys(params).some(key=>!['enabled','external_url'].includes(key)))throw fault('不支持的手机连接设置。');
        if(params.enabled!==undefined && typeof params.enabled!=='boolean')throw fault('enabled 必须是布尔值。');
        if(params.external_url!==undefined)this.state.external_url=mobileExternalUrl(params.external_url);
        if(params.enabled!==undefined)this.state.enabled=params.enabled;
        if(!this.state.enabled){for(const device of this.state.devices)device.revision++;this.state.pending=null;}
        this.state.revision++;this.persist();
        if(this.state.enabled)await this.listen();else {await this.closeListener();await this.product.cancelMobileCommands?.();}
        return this.status();
      }
      if(method==='mobile.pair.create') {
        if(!this.server)throw fault('请先启用手机入口。','not_ready',409);
        const name=params.device_name===undefined?'我的手机':text(params.device_name,'设备名称',80);
        if(params.base_url && params.base_url!==this.state.external_url && params.base_url!==this.url)throw fault('请先在手机连接中配置访问地址。');
        const code=randomBytes(8).toString('hex').toUpperCase(),expires_at=this.now()+TTL;
        this.state.pending={hash:hash(code),name,expires_at};this.state.revision++;this.persist();
        const url=(this.state.external_url||this.url)+'#pair='+code;
        return {code,expires_at,url,qr_content:url,local_only:!this.state.external_url};
      }
      if(method==='mobile.devices.revoke') {
        const id=text(params.device_id,'设备',100),device=this.state.devices.find(d=>d.id===id);
        if(!device)throw fault('手机设备不存在。','not_found',404);
        if(!device.revoked){device.revoked=true;device.revision++;this.state.revision++;delete this.state.device_preferences[id];this.persist();}
        await this.product.cancelMobileCommands?.(id);
        return this.status();
      }
      throw fault('不支持的手机管理操作。','method_not_allowed',403);
    });
  }
  checkOrigin(request) {
    const address=this.server?.address();if(!address)throw fault('手机入口已停用。','not_ready',503);
    const origins=new Set([`http://127.0.0.1:${address.port}`,`http://localhost:${address.port}`]);
    if(this.state.external_url)origins.add(new URL(this.state.external_url).origin);
    const hosts=new Set([...origins].map(origin=>new URL(origin).host));
    if(!hosts.has(request.headers.host))throw fault('请求主机不匹配。','invalid_host',403);
    if(request.headers.origin && !origins.has(request.headers.origin))throw fault('拒绝跨来源请求。','invalid_origin',403);
    if(request.headers['sec-fetch-site']==='cross-site')throw fault('拒绝跨站请求。','invalid_origin',403);
  }
  authenticate(request) {
    const token=request.headers.authorization?.startsWith('Bearer ')?request.headers.authorization.slice(7):null;
    const device=token && this.state.devices.find(d=>secretEqual(hash(token),d.token_hash));
    if(!device || device.revoked || device.expires_at<=this.now())throw fault('手机连接已失效，请在电脑重新配对。','unauthorized',401);
    const principal=Object.freeze({origin:'mobile',id:device.id,device_id:device.id,revision:device.revision,expires_at:device.expires_at});
    this.principals.add(principal);
    if(this.now()-(device.last_seen??0)>60_000){device.last_seen=this.now();this.persist();}
    return principal;
  }
  authorize(principal) {
    if(this.closed || !this.state.enabled || !this.principals.has(principal))return false;
    const device=this.state.devices.find(d=>d.id===principal.id);
    return !!device && !device.revoked && device.revision===principal.revision && device.expires_at>this.now();
  }
  require(principal) {if(!this.authorize(principal))throw fault('手机连接已失效。','unauthorized',401);}
  async pair(params,request) {
    return this.serial(()=>{
      const address=request.socket.remoteAddress??'local',now=this.now();
      for(const [key,value] of this.attempts)if(value.until<=now)this.attempts.delete(key);
      const attempt=this.attempts.get(address)??{count:0,until:now+10*60_000};
      if(++attempt.count>20)throw fault('配对尝试过多，请稍后重试。','rate_limited',429);
      this.attempts.set(address,attempt);
      const pending=this.state.pending,code=typeof params.code==='string'?params.code.replace(/[-\s]/g,'').toUpperCase():'';
      if(!pending || pending.expires_at<=now || !secretEqual(hash(code),pending.hash))throw fault('配对码无效或已过期，请在电脑重新生成。','pairing_invalid',403);
      const name=params.device_name===undefined?pending.name:text(params.device_name,'设备名称',80);
      const live=this.state.devices.filter(d=>!d.revoked && d.expires_at>now);
      if(live.length>=20)throw fault('已连接的手机过多，请先撤销不再使用的设备。','device_limit',409);
      const token=randomBytes(32).toString('base64url'),device={id:randomUUID(),name,token_hash:hash(token),revision:1,created_at:now,last_seen:now,expires_at:now+DEVICE_TTL,revoked:false};
      this.state.devices=[...this.state.devices.filter(d=>!d.revoked && d.expires_at>now),device];this.state.pending=null;
      this.state.revision++;this.persist();
      return {token,device:this.publicDevice(device),preferences:this.preferenceResult(device.id)};
    });
  }
  preferenceResult(id) {
    const user=this.state.user_preferences,device=this.state.device_preferences[id]??{};
    return {effective:{...DEFAULTS,...user,...device},user:{...user},device:{...device},revision:this.state.revision};
  }
  async preferenceRequest(method,params,principal) {
    if(method==='mobile.preferences.get')return this.preferenceResult(principal.id);
    return this.serial(()=>{
      this.require(principal);
      if(params.expected_revision!==this.state.revision)throw fault('设置已变化，请刷新后重试。','CONFLICT',409);
      if(!['device','user'].includes(params.scope))throw fault('请选择当前设备或默认设置。');
      if(method==='mobile.preferences.set') {
        const patch=preferences(params.patch);
        if(params.scope==='user')this.state.user_preferences={...this.state.user_preferences,...patch};
        else this.state.device_preferences[principal.id]={...this.state.device_preferences[principal.id],...patch};
      } else if(method==='mobile.preferences.reset') {
        if(params.scope==='user')this.state.user_preferences={};else delete this.state.device_preferences[principal.id];
      } else throw fault('不支持的界面操作。','method_not_allowed',403);
      this.state.revision++;this.persist();return this.preferenceResult(principal.id);
    });
  }
  async dispatch(method,params,principal) {
    this.require(principal);
    if(typeof method!=='string' || !isObject(params))throw fault('手机操作格式无效。');
    if(method.startsWith('mobile.preferences.'))return this.preferenceRequest(method,params,principal);
    if(method==='mobile.status')return {ready:true,device:this.publicDevice(this.state.devices.find(d=>d.id===principal.id)),connected_at:this.now()};
    if(method==='conversation.list') {
      const limit=integer(params.limit??100,'列表数量',1,200),offset=integer(params.offset??0,'列表位置',0,1_000_000);
      const result=await this.product.rpc('conversation.list',{limit,offset,archived_filter:params.archived_filter??'all'});
      return {...result,has_more:result.conversations.length===limit,next_offset:offset+result.conversations.length};
    }
    if(method==='conversation.create') {
      const requestId=text(params.request_id??randomUUID(),'创建请求',128);
      const title=params.title===undefined?undefined:text(params.title,'会话名称',512);
      const conversation=await this.product.rpc('conversation.ensure',{...(title===undefined?{}:{title}),conversation_id:'conversation_mobile_'+hash(principal.id+':'+requestId).slice(0,32)});
      return {conversation};
    }
    if(method==='conversation.get')return {conversation:await this.product.conversation(text(params.conversation_id,'会话'))};
    if(method==='conversation.update') {
      const id=text(params.conversation_id,'会话'),patch=params.patch;
      if(!isObject(patch) || Object.keys(patch).some(key=>!['title','pinned','archived'].includes(key)))throw fault('不支持的会话修改。');
      return this.product.serial(id,async()=>{
        this.require(principal);const conversation=await this.product.conversation(id);
        if(patch.archived===true && this.product.handles?.get(conversation.session_id)?.agent.status!=='idle'
          && this.product.handles?.has(conversation.session_id))throw fault('请先停止当前回复，再归档会话。','conversation_busy',409);
        const updated=await this.product.rpc('conversation.patch',{conversation_id:id,expected_version:params.expected_version,patch});
        this.product.broadcast?.('session-metadata',{session_id:id,metadata_version:updated.metadata_version});
        return {conversation:updated};
      });
    }
    if(method==='conversation.history') {
      const id=text(params.conversation_id,'会话'),limit=integer(params.limit??50,'历史数量',1,100);
      const history=await this.product.history(id);this.require(principal);
      const messages=history.messages.filter(m=>['user','assistant'].includes(m.role) && (!params.before_seq || m.seq<params.before_seq));
      const selected=messages.slice(-limit).map(m=>({id:m.id,role:m.role,seq:m.seq,time:m.time,command_id:m.command_id,
        ...(m.role==='user' && m.input_origin ? {input_origin:m.input_origin} : {}),
        text:String(m.text??'').slice(0,32_000),content:[{type:'text',text:String(m.text??'').slice(0,32_000)}]}));
      return {messages:selected,gaps:history.gaps,active_command_ids:history.active_command_ids,in_flight:history.in_flight,
        has_more:messages.length>limit,oldest_seq:selected[0]?.seq??null};
    }
    if(method==='conversation.send')return this.product.sendMobile(params,principal);
    if(method==='conversation.stop') {
      const id=text(params.conversation_id,'会话');await this.product.conversation(id);this.require(principal);
      return this.product.cancel(id,params.command_id);
    }
    if(method==='inbox.list') return this.product.rpc('inbox.list',{});
    if(method==='inbox.ack') return this.product.rpc('inbox.ack',{delivery_id:params.delivery_id,state:params.state});
    if(method==='workspace.roots') {
      const result=await this.product.rpc('workspace.roots');
      return {roots:(result.roots??[]).map(({id,root_id,label,name,writable})=>({id:id??root_id,root_id:root_id??id,label:label??name??id,writable:!!writable}))};
    }
    throw fault('该操作未向手机开放。','method_not_allowed',403);
  }
  async handle(request,response) {
    security(response);this.checkOrigin(request);
    const url=new URL(request.url,'http://127.0.0.1'),path=url.pathname;
    if(path==='/api/mobile/pair' && request.method==='POST')return reply(response,200,{result:await this.pair(await input(request),request)});
    if(path==='/api/mobile/status' && request.method==='GET')return reply(response,200,{ready:!!this.server,product:'Mirally',pairing_required:true});
    if(path==='/api/mobile/rpc' && request.method==='POST') {
      const principal=this.authenticate(request),{method,params={}}=await input(request);
      const result=await this.dispatch(method,params,principal);this.require(principal);
      return reply(response,200,{result});
    }
    if(request.method!=='GET' || !(['/mobile','/mobile/','/mobile.html','/favicon.svg'].includes(path) || /^\/assets\/[a-zA-Z0-9_.\/-]+$/.test(path)))throw fault('手机页面不存在。','not_found',404);
    const directory=realpathSync(this.product.uiDirectory),file=resolve(directory,['/mobile','/mobile/','/mobile.html'].includes(path)?'mobile.html':'.'+path);
    let actual;try{actual=realpathSync(file);}catch{throw fault('手机页面尚未构建，请重新准备前端。','mobile_ui_not_built',503);}
    if(!actual.startsWith(directory+sep))throw fault('不允许访问该文件。','not_found',404);
    const type=MIME[extname(actual)];if(!type)throw fault('不允许访问该文件类型。','not_found',404);
    const data=await readFile(actual);response.writeHead(200,{'Content-Type':type,'Content-Length':data.length});response.end(data);
  }
}
