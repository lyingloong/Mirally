import {html, nothing, type PropertyValues} from 'lit';
import {customElement, state} from 'lit/decorators.js';
import {live} from 'lit/directives/live.js';
import {unsafeHTML} from 'lit/directives/unsafe-html.js';
import {OpenClawLightDomElement} from '../lit/openclaw-element.ts';
import {toSanitizedMarkdownHtml} from '../components/markdown.ts';
import '../pages/mirally/work-panel.ts';
import {createMobileRpc,DEFAULT_MOBILE_PREFERENCES,MobileApiError,mobileNavigation,pairingCodeFromHash,pairMobile,readMobileToken,storeMobileToken,timeLabel,type MobilePreferences,type Rpc} from './api.ts';

type Data=Record<string,any>;
type View='work'|'conversations'|'inbox'|'settings';
const LABELS:Record<string,string>={work:'工作',conversations:'会话',inbox:'消息',settings:'设置'};
const LAST_VIEW_KEY='mirally.mobile.view.v1';
const draftKey=(id:string)=>'mirally.mobile.draft.'+id;
function readSession(key:string) {try{return sessionStorage.getItem(key)||'';}catch{return '';}}
function writeSession(key:string,value:string) {try{sessionStorage.setItem(key,value);}catch{}}
const conversationId=(item:Data)=>item.conversation_id||item.id;
const messageText=(item:Data)=>typeof item.text==='string'?item.text:typeof item.content==='string'?item.content:(item.content||[]).filter((block:Data)=>block.type==='text').map((block:Data)=>block.text).join('');

/** Restricted phone client. Server state and the native DSH transcript remain authoritative. */
@customElement('mirally-mobile-app')
export class MirallyMobileApp extends OpenClawLightDomElement {
  @state() private token=readMobileToken();
  @state() private rpc:Rpc=createMobileRpc(this.token);
  @state() private view:View='work';
  @state() private device:Data|null=null;
  @state() private preferences:MobilePreferences={...DEFAULT_MOBILE_PREFERENCES};
  @state() private preferenceState:Data={revision:0,user:{},device:{}};
  @state() private preferenceScope:'device'|'user'='device';
  @state() private workSettings:Data|null=null;
  @state() private code=pairingCodeFromHash(location.hash);
  @state() private deviceName='我的手机';
  @state() private busy=false;
  @state() private loading=false;
  @state() private error='';
  @state() private notice='';
  @state() private lastContact=0;
  @state() private connected=false;
  @state() private conversations:Data[]=[];
  @state() private conversationHasMore=false;
  @state() private nextConversationOffset=0;
  @state() private search='';
  @state() private archiveFilter='active';
  @state() private currentConversation='';
  @state() private currentConversationMeta:Data|null=null;
  @state() private messages:Data[]=[];
  @state() private history:Data={};
  @state() private draft='';
  @state() private rename='';
  @state() private editingTitle=false;
  @state() private inbox:Data[]=[];
  @state() private moreOpen=false;
  private commandId='';
  private commandText='';
  private conversationCreateId='';
  private actionError=false;
  private epoch=0;
  private timer:ReturnType<typeof setTimeout>|undefined;
  private readonly visibilityChanged=()=>{this.clearPoll();if(!document.hidden&&this.token)void this.refresh();};
  override connectedCallback(){super.connectedCallback();document.addEventListener('visibilitychange',this.visibilityChanged);if(location.hash)history.replaceState(null,'',location.pathname+location.search);if(this.token)void this.boot();}
  override disconnectedCallback(){++this.epoch;this.clearPoll();document.removeEventListener('visibilitychange',this.visibilityChanged);super.disconnectedCallback();}
  protected override updated(changed:PropertyValues){if(changed.has('view')&&this.token){writeSession(LAST_VIEW_KEY,this.view);void this.refresh();}}
  private clearPoll(){if(this.timer!==undefined)clearTimeout(this.timer);this.timer=undefined;}
  private schedule(){this.clearPoll();if(this.isConnected&&this.token&&!document.hidden)this.timer=setTimeout(()=>void this.refresh(),3500);}
  private fail(error:unknown,action=false){this.connected=false;this.actionError=action;this.error=error instanceof Error?error.message:'操作未完成，请刷新。';if(error instanceof MobileApiError&&error.status===401){this.token='';storeMobileToken('');++this.epoch;this.clearPoll();}}
  private acceptPreferences(result:Data){this.preferenceState=result;this.preferences={...DEFAULT_MOBILE_PREFERENCES,...result.effective};}
  private async boot(){
    const epoch=++this.epoch;this.loading=true;
    try{const preferences=await this.rpc<Data>('mobile.preferences.get');if(!this.isConnected||epoch!==this.epoch)return;
      this.acceptPreferences(preferences);this.device=preferences.device_info||this.device;
      const last=readSession(LAST_VIEW_KEY);this.view=(this.preferences.home==='last'&&last in LABELS?last:this.preferences.home==='conversations'?'conversations':'work') as View;
      this.connected=true;this.lastContact=Date.now();if(!this.actionError)this.error='';
    }catch(error){if(this.isConnected&&epoch===this.epoch)this.fail(error);}
    finally{if(this.isConnected&&epoch===this.epoch){this.loading=false;void this.refresh();}}
  }
  private async pair(event:SubmitEvent){
    event.preventDefault();if(this.busy)return;this.busy=true;this.error='';
    try{const result=await pairMobile(this.code.trim(),this.deviceName.trim()||'我的手机');this.token=result.token;storeMobileToken(this.token);this.rpc=createMobileRpc(this.token);this.device=result.device;this.code='';await this.boot();}
    catch(error){this.fail(error);}finally{this.busy=false;}
  }
  private navigate(view:View){this.view=view;this.moreOpen=false;this.error='';this.notice='';if(view==='settings')void this.loadSettings();}
  private async loadSettings(){try{const preferences=await this.rpc<Data>('mobile.preferences.get');this.acceptPreferences(preferences);const result=await this.rpc<Data>('work.settings.get');this.workSettings=result;this.connected=true;this.lastContact=Date.now();}catch(error){this.fail(error);}}
  private async refresh(){
    if(!this.token||!this.isConnected||document.hidden||this.busy){this.schedule();return;}
    const epoch=++this.epoch,view=this.view,id=this.currentConversation;
    try{
      if(view==='conversations'){
        const result=await this.rpc<Data>('conversation.list',{archived_filter:this.archiveFilter,limit:100});
        if(epoch!==this.epoch||!this.isConnected)return;
        const received=result.conversations||[],ids=new Set(received.map(conversationId));
        const retained=this.conversations.filter(item=>!ids.has(conversationId(item))&&(this.archiveFilter==='all'||(this.archiveFilter==='archived'?item.archived:!item.archived)));
        this.conversations=[...received,...retained];
        if(!retained.length){this.conversationHasMore=result.has_more===true;this.nextConversationOffset=result.next_offset||received.length;}
        if(id){const selected=await this.rpc<Data>('conversation.get',{conversation_id:id});if(epoch!==this.epoch||id!==this.currentConversation||!this.isConnected)return;
          this.acceptConversation(selected.conversation);
          const history=await this.rpc<Data>('conversation.history',{conversation_id:id,limit:50});if(epoch!==this.epoch||id!==this.currentConversation||!this.isConnected)return;
          const older=this.messages.filter(item=>Number(item.seq)<Number(history.oldest_seq||0));this.messages=[...older,...(history.messages||[])];this.history={...history,has_more:older.length?this.history.has_more:history.has_more};}
      }else if(view==='inbox'){
        const result=await this.rpc<Data>('inbox.list');if(epoch!==this.epoch||!this.isConnected)return;this.inbox=result.items||[];
      }else if(view==='settings'){
        const result=await this.rpc<Data>('mobile.preferences.get');if(epoch!==this.epoch||!this.isConnected)return;this.acceptPreferences(result);
      }else{await this.rpc('mobile.preferences.get');if(epoch!==this.epoch||!this.isConnected)return;}
      this.connected=true;this.lastContact=Date.now();if(!this.actionError)this.error='';
    }catch(error){if(epoch===this.epoch&&this.isConnected)this.fail(error);}
    finally{if(epoch===this.epoch&&this.isConnected)this.schedule();}
  }
  private async act(action:()=>Promise<unknown>,notice=''){
    if(this.busy)return;const epoch=++this.epoch;this.busy=true;this.error='';this.notice='';this.actionError=false;this.clearPoll();
    try{await action();if(this.isConnected&&epoch===this.epoch){this.notice=notice;this.connected=true;this.lastContact=Date.now();}}
    catch(error){if(this.isConnected&&epoch===this.epoch)this.fail(error,true);}
    finally{if(this.isConnected){this.busy=false;void this.refresh();}}
  }
  private openConversation(id:string){++this.epoch;this.currentConversation=id;this.currentConversationMeta=null;this.messages=[];this.history={};this.editingTitle=false;this.draft=readSession(draftKey(id));
    let saved:Data={};try{saved=JSON.parse(readSession(draftKey(id)+'.command')||'{}');}catch{}
    this.commandId=typeof saved.id==='string'?saved.id:'';this.commandText=typeof saved.text==='string'?saved.text:'';this.navigate('conversations');void this.refresh();}
  private async older(){if(this.busy||!this.history.has_more)return;await this.act(async()=>{
    const result=await this.rpc<Data>('conversation.history',{conversation_id:this.currentConversation,limit:50,before_seq:this.messages[0]?.seq});
    const ids=new Set(this.messages.map(item=>item.seq));this.messages=[...(result.messages||[]).filter((item:Data)=>!ids.has(item.seq)),...this.messages];this.history={...this.history,has_more:result.has_more};
  });}
  private moreConversations(){void this.act(async()=>{
    const result=await this.rpc<Data>('conversation.list',{archived_filter:this.archiveFilter,limit:100,offset:this.nextConversationOffset});
    const existing=new Set(this.conversations.map(conversationId));this.conversations=[...this.conversations,...(result.conversations||[]).filter((item:Data)=>!existing.has(conversationId(item)))];
    this.conversationHasMore=result.has_more===true;this.nextConversationOffset=result.next_offset||this.nextConversationOffset+(result.conversations||[]).length;
  });}
  private send(event:SubmitEvent){event.preventDefault();const text=this.draft.trim(),id=this.currentConversation;if(!text||this.busy)return;
    if(text!==this.commandText||!this.commandId){this.commandText=text;this.commandId=crypto.randomUUID();}
    writeSession(draftKey(id)+'.command',JSON.stringify({id:this.commandId,text:this.commandText}));
    const command_id=this.commandId;
    void this.act(async()=>{await this.rpc('conversation.send',{conversation_id:id,text,command_id});writeSession(draftKey(id),'');writeSession(draftKey(id)+'.command','');if(this.currentConversation===id){this.draft='';this.commandText='';this.commandId='';}},'已提交到同一会话。');
  }
  private createConversation(){
    if(!this.conversationCreateId)this.conversationCreateId=crypto.randomUUID();
    void this.act(async()=>{const result=await this.rpc<Data>('conversation.create',{request_id:this.conversationCreateId});this.conversationCreateId='';this.openConversation(conversationId(result.conversation||result));});
  }
  private acceptConversation(conversation:Data) {
    if(!conversation || conversationId(conversation)!==this.currentConversation)return;
    this.currentConversationMeta=conversation;
    const inFilter=this.archiveFilter==='all'||(this.archiveFilter==='archived'?conversation.archived:!conversation.archived);
    this.conversations=this.conversations.flatMap(item=>conversationId(item)!==conversationId(conversation)?[item]:inFilter?[conversation]:[]);
  }
  private patchConversation(patch:Data){const conversation=this.currentConversationMeta,id=this.currentConversation;if(!conversation||conversationId(conversation)!==id)return;
    void this.act(async()=>{const result=await this.rpc<Data>('conversation.update',{conversation_id:id,expected_version:conversation.metadata_version,patch});
      if(this.currentConversation===id){this.acceptConversation(result.conversation);this.editingTitle=false;}
    },'会话已更新。');}
  private savePreferences(patch:Data){void this.act(async()=>{this.acceptPreferences(await this.rpc<Data>('mobile.preferences.set',{scope:this.preferenceScope,expected_revision:this.preferenceState.revision,patch}));},'显示偏好已保存。');}
  private resetPreferences(){void this.act(async()=>{this.acceptPreferences(await this.rpc<Data>('mobile.preferences.reset',{scope:this.preferenceScope,expected_revision:this.preferenceState.revision}));},this.preferenceScope==='device'?'已移除这台设备的覆盖，使用用户默认。':'用户默认已恢复。');}
  private reorder(id:string,direction:number){const navigation=[...this.preferences.navigation];const index=navigation.indexOf(id),target=index+direction;if(index<0||target<0||target>=navigation.length)return;[navigation[index],navigation[target]]=[navigation[target],navigation[index]];this.savePreferences({navigation});}
  private toggleHidden(id:string,hidden:boolean){this.savePreferences({hidden:hidden?[...new Set([...this.preferences.hidden,id])]:this.preferences.hidden.filter(item=>item!==id)});}
  private notificationPatch(patch:Data){void this.act(async()=>{this.workSettings=await this.rpc<Data>('work.settings.update',{expected_revision:this.workSettings?.settings?.revision,patch});},'工作通知策略已保存。');}
  private pairView(){return html`<main class="mirally-mobile-pair"><img src="/favicon.svg" alt="Mirally" width="48" height="48"><h1>把电脑上的工作带在身边</h1><p>在电脑 Mirally 的「消息渠道 → 手机网页（可选扩展）」中生成配对码，然后连接这台手机。</p>
    ${this.error?html`<p class="callout danger" role="alert">${this.error}</p>`:nothing}<form @submit=${this.pair} class="mirally-product-form">
      <label class="mirally-product-field">配对码<input name="code" autocomplete="one-time-code" required maxlength="128" .value=${live(this.code)} @input=${(e:Event)=>this.code=(e.target as HTMLInputElement).value} ?disabled=${this.busy}></label>
      <label class="mirally-product-field">设备名称<input name="device_name" maxlength="80" .value=${live(this.deviceName)} @input=${(e:Event)=>this.deviceName=(e.target as HTMLInputElement).value} ?disabled=${this.busy}></label>
      <button type="submit" class="btn primary" ?disabled=${this.busy||!this.code.trim()}>${this.busy?'正在连接…':'配对并进入'}</button>
    </form><p class="mirally-product-meta">电脑需保持 Mirally 运行。此页仅连接电脑已有工作与会话。关闭此浏览器会话后可能需要重新配对。</p></main>`;}
  private conversationsView(){
    const active=this.currentConversationMeta&&conversationId(this.currentConversationMeta)===this.currentConversation?this.currentConversationMeta:null;
    if(this.currentConversation)return html`<section class="mirally-mobile-conversation"><div class="mirally-product-actions"><button class="btn" @click=${()=>{this.currentConversation='';this.currentConversationMeta=null;this.messages=[];void this.refresh();}}>返回会话列表</button><button class="btn" @click=${()=>this.refresh()} ?disabled=${this.busy}>刷新</button></div>
      <h2>${active?.title||'会话'}</h2><details class="mirally-work-card"><summary>管理会话</summary><div class="mirally-product-form">
        ${this.editingTitle?html`<form class="mirally-product-actions" @submit=${(e:SubmitEvent)=>{e.preventDefault();if(this.rename.trim())this.patchConversation({title:this.rename.trim()});}}><input aria-label="会话名称" maxlength="512" .value=${live(this.rename)} @input=${(e:Event)=>this.rename=(e.target as HTMLInputElement).value}><button class="btn" ?disabled=${this.busy}>保存名称</button></form>`:html`<button class="btn" ?disabled=${!active||this.busy} @click=${()=>{this.rename=active?.title||'';this.editingTitle=true;}}>重命名</button>`}
        <div class="mirally-product-actions"><button class="btn" ?disabled=${!active||this.busy} @click=${()=>this.patchConversation({pinned:!active?.pinned})}>${active?.pinned?'取消固定':'固定会话'}</button><button class="btn" ?disabled=${!active||this.busy} @click=${()=>this.patchConversation({archived:!active?.archived})}>${active?.archived?'恢复会话':'归档会话'}</button></div>
      </div></details>
      ${this.history.has_more?html`<button class="btn" ?disabled=${this.busy} @click=${()=>this.older()}>读取更早的记录</button>`:nothing}
      ${(this.history.gaps||[]).length?html`<p class="callout">部分历史暂时无法读取，展示的是当前可用记录。</p>`:nothing}
      <div class="mirally-mobile-transcript" aria-label="会话记录">${this.messages.filter(item=>['user','assistant'].includes(item.role)).map(item=>html`<article class="mirally-mobile-message" data-role=${item.role}><div class="mirally-product-meta">${item.role==='user'?'你':'Mirally'} · ${timeLabel(item.timestamp||item.time)}</div><div class="markdown">${unsafeHTML(toSanitizedMarkdownHtml(messageText(item),{codeBlockChrome:'none',codeBlockInteraction:'static',remoteImages:false,fileLinks:false,sessionLinks:false}))}</div></article>`)}</div>
      ${this.history.in_flight||this.history.active_command_ids?.length?html`<div class="mirally-product-actions"><span role="status">正在回复…</span><button class="btn" ?disabled=${this.busy} @click=${()=>this.act(()=>this.rpc('conversation.stop',{conversation_id:this.currentConversation}),'已请求停止回复。')}>停止回复</button></div>`:nothing}
      ${!active?html`<p role="status" class="callout">正在核对会话状态，请稍候或刷新。</p>`:active.archived?html`<p class="callout">此会话已归档。恢复后可以继续对话。</p>`:html`<form class="mirally-mobile-composer" @submit=${this.send}><label class="mirally-product-field">继续聊<textarea aria-label="消息内容" .value=${live(this.draft)} @input=${(e:Event)=>{this.draft=(e.target as HTMLTextAreaElement).value;writeSession(draftKey(this.currentConversation),this.draft);}} maxlength="24000" ?disabled=${this.busy}></textarea></label><button type="submit" class="btn primary" ?disabled=${this.busy||!this.draft.trim()||!this.connected}>发送</button></form>`}
      <p class="mirally-product-meta">与电脑共用同一份聊天记录。此处切换会话不会更改微信后续消息的目标。</p>
    </section>`;
    const list=this.conversations.filter(item=>(item.title||'未命名会话').toLocaleLowerCase().includes(this.search.toLocaleLowerCase())).sort((a,b)=>(this.preferences.conversation_sort==='pinned'?Number(!!b.pinned)-Number(!!a.pinned):0)||Number(b.updated_at)-Number(a.updated_at));
    return html`<section><div class="mirally-product-actions"><button class="btn primary" ?disabled=${this.busy} @click=${()=>this.createConversation()}>新建会话</button>
      <select aria-label="会话范围" .value=${this.archiveFilter} @change=${(e:Event)=>{this.archiveFilter=(e.target as HTMLSelectElement).value;this.conversations=[];this.conversationHasMore=false;this.nextConversationOffset=0;void this.refresh();}}><option value="active">使用中的会话</option><option value="archived">已归档</option><option value="all">全部</option></select></div>
      <label class="mirally-product-field mirally-mobile-search">筛选已加载的会话标题<input type="search" placeholder="按标题筛选，不搜索聊天正文" .value=${live(this.search)} @input=${(e:Event)=>this.search=(e.target as HTMLInputElement).value}></label>
      <div class="mirally-work-list">${list.map(item=>html`<button class="mirally-work-card mirally-work-list-item" data-testid="mobile-conversation" @click=${()=>this.openConversation(conversationId(item))}><strong>${item.pinned?'★ ':''}${item.title||'未命名会话'}</strong><small class="mirally-product-meta">${timeLabel(item.updated_at)}${item.archived?' · 已归档':''}</small></button>`)}</div>${!list.length?html`<p class="mirally-product-empty">没有符合条件的会话。</p>`:nothing}${this.conversationHasMore?html`<button class="btn" ?disabled=${this.busy} @click=${()=>this.moreConversations()}>加载更多会话</button>`:nothing}<p class="mirally-product-meta">已加载 ${this.conversations.length} 项。标题筛选只覆盖已加载项目。</p></section>`;
  }
  private inboxView(){return html`<section class="mirally-work-list">${this.inbox.length?this.inbox.map(item=>html`<article class="mirally-work-card mirally-product-form"><h2>${item.title}</h2><p class="mirally-product-body">${item.body}</p><small>${timeLabel(item.updated_at)} · ${item.state==='unread'?'未读':'已读'}</small><div class="mirally-product-actions">${item.state==='unread'?html`<button class="btn" ?disabled=${this.busy} @click=${()=>this.act(()=>this.rpc('inbox.ack',{delivery_id:item.delivery_id,state:'read'}))}>标记已读</button>`:nothing}${item.conversation_id&&!item.withdrawn?html`<button class="btn" @click=${()=>this.openConversation(item.conversation_id)}>查看会话</button>`:nothing}</div></article>`):html`<p class="mirally-product-empty">暂时没有消息。完成、异常与需要确认的事项会保存在这里。</p>`}</section>`;}
  private settingsView(){const settings=this.workSettings?.settings;return html`<div class="mirally-mobile-settings"><section class="mirally-work-card mirally-product-form"><h2>按你的习惯呈现</h2><label class="mirally-product-field">保存到<select .value=${this.preferenceScope} @change=${(e:Event)=>this.preferenceScope=(e.target as HTMLSelectElement).value as 'device'|'user'}><option value="device">这台手机的覆盖设置</option><option value="user">用户默认（没有设备覆盖时使用）</option></select></label>
      <p class="mirally-product-meta">当前展示生效后的设置。设备覆盖优先于用户默认；更改默认不会切换当前页面。</p>
      <label class="mirally-product-field">默认首页<select .value=${this.preferences.home} ?disabled=${this.busy} @change=${(e:Event)=>this.savePreferences({home:(e.target as HTMLSelectElement).value})}><option value="work">工作</option><option value="conversations">会话</option><option value="last">继续上次</option></select></label>
      <h3>导航顺序与显示</h3>${this.preferences.navigation.map((id,index)=>html`<div class="mirally-mobile-nav-setting"><span>${LABELS[id]}</span><div><button class="btn" aria-label=${`上移${LABELS[id]}`} ?disabled=${this.busy||index===0} @click=${()=>this.reorder(id,-1)}>↑</button><button class="btn" aria-label=${`下移${LABELS[id]}`} ?disabled=${this.busy||index===this.preferences.navigation.length-1} @click=${()=>this.reorder(id,1)}>↓</button><label><input type="checkbox" ?disabled=${this.busy||id==='settings'} .checked=${id==='settings'||!this.preferences.hidden.includes(id)} @change=${(e:Event)=>this.toggleHidden(id,!(e.target as HTMLInputElement).checked)}>显示</label></div></div>`)}
      <label class="mirally-product-field">工作排序<select .value=${this.preferences.work_sort} ?disabled=${this.busy} @change=${(e:Event)=>this.savePreferences({work_sort:(e.target as HTMLSelectElement).value})}><option value="attention">需要处理的优先</option><option value="updated">最近更新</option></select></label>
      <label><input type="checkbox" .checked=${this.preferences.show_completed} ?disabled=${this.busy} @change=${(e:Event)=>this.savePreferences({show_completed:(e.target as HTMLInputElement).checked})}>显示已完成或结束的工作</label>
      <label class="mirally-product-field">会话排序<select .value=${this.preferences.conversation_sort} ?disabled=${this.busy} @change=${(e:Event)=>this.savePreferences({conversation_sort:(e.target as HTMLSelectElement).value})}><option value="updated">最近活动</option><option value="pinned">固定优先，再按最近活动</option></select></label>
      <button class="btn" ?disabled=${this.busy} @click=${()=>this.resetPreferences()}>${this.preferenceScope==='device'?'恢复使用用户默认':'恢复初始用户默认'}</button>
    </section><section class="mirally-work-card mirally-product-form"><h2>工作通知</h2><p class="mirally-product-meta">属于电脑上的投递策略，所有入口共享；关闭通知不会停止工作。消息页不是系统推送。</p>
      ${settings?html`${[['completed','运行完成'],['failed','运行异常'],['approval','需要确认']].map(([key,label])=>html`<label><input type="checkbox" .checked=${settings.notifications?.[key]!==false} ?disabled=${this.busy} @change=${(e:Event)=>this.notificationPatch({notifications:{...settings.notifications,[key]:(e.target as HTMLInputElement).checked}})}>${label}</label>`)}
      ${[['inbox','Mirally 消息页'],['weixin','微信（需要已绑定且通道可投递）']].map(([key,label])=>html`<label><input type="checkbox" .checked=${settings.channels?.[key]===true} ?disabled=${this.busy} @change=${(e:Event)=>this.notificationPatch({channels:{...settings.channels,[key]:(e.target as HTMLInputElement).checked}})}>${label}</label>`)}`:html`<button class="btn" @click=${()=>this.loadSettings()}>读取通知策略</button>`}
    </section><section class="mirally-work-card mirally-product-form"><h2>入口与连接</h2><div class="mirally-product-actions">${Object.entries(LABELS).map(([id,label])=>html`<button class="btn" @click=${()=>this.navigate(id as View)}>${label}</button>`)}</div><p>最后成功连接：${timeLabel(this.lastContact)}</p><p class="mirally-product-meta">关闭页面不会停止电脑的运行。配对设备可在电脑「消息渠道」中撤销。</p><button class="btn" @click=${()=>{this.token='';this.rpc=createMobileRpc('');storeMobileToken('');this.messages=[];this.conversations=[];this.currentConversationMeta=null;this.inbox=[];++this.epoch;this.clearPoll();}}>退出此浏览器连接</button></section></div>`;}
  override render(){if(!this.token)return this.pairView();const navigation=mobileNavigation(this.preferences),hidden=Object.keys(LABELS).filter(id=>!navigation.includes(id));
    return html`<div class="mirally-mobile-shell"><header class="mirally-mobile-header"><div><span class="mirally-mobile-brand"><img src="/favicon.svg" alt="" width="24" height="24">Mirally</span><h1>${LABELS[this.view]}</h1></div><button class="mirally-mobile-connection" data-connected=${this.connected} @click=${()=>this.refresh()} title="刷新连接状态">${this.connected?'电脑已连接':'连接待恢复'}<small>${this.lastContact?timeLabel(this.lastContact):'正在连接…'}</small></button></header>
      <main class="mirally-mobile-main">${this.error?html`<p class="callout danger" role="alert">${this.error}</p>`:nothing}${this.notice?html`<p class="callout" role="status">${this.notice}</p>`:nothing}${this.loading?html`<p role="status">正在读取电脑上的状态…</p>`:nothing}
      ${this.view==='work'?html`<mirally-work-panel .rpc=${this.rpc} .sort=${this.preferences.work_sort} .showCompleted=${this.preferences.show_completed} .openConversation=${(id:string)=>this.openConversation(id)}></mirally-work-panel>`:this.view==='conversations'?this.conversationsView():this.view==='inbox'?this.inboxView():this.settingsView()}</main>
      ${this.moreOpen?html`<div class="mirally-mobile-more" role="group" aria-label="更多入口">${hidden.map(id=>html`<button class="btn" @click=${()=>this.navigate(id as View)}>${LABELS[id]}</button>`)}</div>`:nothing}
      <nav class="mirally-mobile-nav" aria-label="主要导航">${navigation.map(id=>html`<button class="btn" aria-current=${this.view===id?'page':nothing} @click=${()=>this.navigate(id as View)}>${LABELS[id]}</button>`)}${hidden.length?html`<button class="btn" aria-expanded=${this.moreOpen} @click=${()=>this.moreOpen=!this.moreOpen}>更多</button>`:nothing}</nav></div>`;
  }
}
