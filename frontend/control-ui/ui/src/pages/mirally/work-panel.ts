import {html, nothing, type PropertyValues} from 'lit';
import {customElement, property, state} from 'lit/decorators.js';
import {live} from 'lit/directives/live.js';
import {OpenClawLightDomElement} from '../../lit/openclaw-element.ts';
import {timeLabel, type Rpc} from '../../mobile/api.ts';
import './product.css';

type Data = Record<string, any>;
const STATE: Record<string, string> = {idle:'待安排',observing:'关注中',needs_approval:'需要确认',starting:'正在启动',running:'正在运行',cancelling:'正在停止',completed:'运行完成',failed:'运行失败',unknown:'需要核对',ended:'已结束关注',archived:'已归档',cancelled:'已停止',interrupted:'运行中断',blocked:'等待处理',timed_out:'运行超时'};
const workId = (work: Data) => work.id || work.work_id;
const PROBLEM = new Set(['failed','unknown','blocked','timed_out','interrupted']);
const needsAttention = (work:Data) => work.status==='needs_approval' || PROBLEM.has(work.status) || PROBLEM.has(work.observation?.state) || (work.pending_approvals||[]).length>0;
const attention = (work:Data) => {
  if((work.pending_approvals||[]).length || work.status==='needs_approval')return 0;
  if(needsAttention(work))return 1;
  if(['starting','running','cancelling'].includes(work.status)||work.observation?.state==='running'||work.conversation_activity==='running')return 2;
  return 3;
};
const primaryStatus = (work:Data) => (work.pending_approvals||[]).length ? '需要确认' : work.observation?.state && work.intent==='watching' ? (STATE[work.observation.state]||'来源状态待核对') : (!work.observation&&!work.latest_run&&work.conversation_activity==='running' ? '对话正在回复' : STATE[work.status]||'状态待核对');
const sourceStatus = (work:Data) => work.observation ? (STATE[work.observation.state]||'状态待核对') : '';
const runStatus = (work:Data) => work.latest_run ? (STATE[work.latest_run.status]||work.latest_run.status||'状态待核对') : '';
const progressLabel = (work:Data) => {
  const progress=work.observation?.progress;
  return typeof progress?.current==='number'&&Number.isFinite(progress.current)&&typeof progress?.total==='number'&&Number.isFinite(progress.total) ? `${progress.current} / ${progress.total}` : '';
};
const mergeUnique = (existing:Data[], batch:Data[], identity:(value:Data)=>string) => {
  const seen=new Set(existing.map(identity));return [...existing,...batch.filter(item=>{const id=identity(item);if(seen.has(id))return false;seen.add(id);return true;})];
};
const nextPage = (page:Data, offset:number, batch:Data[], moreKey:string, offsetKey:string) => {
  const more=!!page[moreKey];const next=Number(page[offsetKey]);
  if(more&&(!Number.isSafeInteger(next)||next<=offset||!batch.length))throw new Error('列表分页未能前进，请刷新后重试。');
  return Number.isSafeInteger(next)&&next>offset?next:offset+batch.length;
};
export function sortWorks(works: Data[], sort: string, showCompleted: boolean) {
  return works.filter(work => showCompleted || !['ended','archived'].includes(work.intent)).sort((a,b) =>
    (sort === 'attention' ? attention(a) - attention(b) : 0) || Number(b.updated_at || 0) - Number(a.updated_at || 0));
}
const errorText = (error: unknown) => error instanceof Error || (error && typeof (error as Data).message==='string') ? (error as Error).message : '操作未完成，请刷新真实状态。';

/** Shared real work surface. Neither the phone nor this component owns execution state. */
@customElement('mirally-work-panel')
export class MirallyWorkPanel extends OpenClawLightDomElement {
  @property({attribute:false}) rpc!: Rpc;
  @property({attribute:false}) openConversation: (id:string) => void = () => {};
  @property({attribute:false}) openPreparation?: () => void;
  @property({type:Boolean}) desktop = false;
  @property() sort = 'attention';
  @property({type:Boolean}) showCompleted = true;
  @property() selected = '';
  @property({attribute:false}) initialFilter = 'current';
  @state() private works: Data[] = [];
  @state() private query = '';
  @state() private filter = 'current';
  @state() private desktopSort = 'attention';
  @state() private loaded = false;
  @state() private hasMore = false;
  private nextOffset = 0;
  private nextRunOffset = 0;
  @state() private detail: Data | null = null;
  @state() private busy = false;
  @state() private loading = false;
  @state() private error = '';
  @state() private notice = '';
  @state() private editingWork = false;
  @state() private showRunForm = false;
  @state() private draft = {source_kind:'none',path:''};
  @state() private runDraft = {script:'',arguments:'',cwd:'.',output_paths:'',timeout:'300'};
  @state() private review: Data | null = null;
  @state() private logs: Record<string,string> = {};
  private timer: ReturnType<typeof setTimeout> | undefined;
  private epoch = 0;
  private actionEpoch = 0;
  private prepareRequest = {content:'',id:''};
  private mutationError = false;
  private readonly visibilityChanged = () => {this.clearPoll(); if (!document.hidden) void this.refresh();};
  override connectedCallback() {super.connectedCallback();document.addEventListener('visibilitychange',this.visibilityChanged);}
  override disconnectedCallback() {++this.epoch;++this.actionEpoch;this.clearPoll();document.removeEventListener('visibilitychange',this.visibilityChanged);super.disconnectedCallback();}
  protected override updated(changed: PropertyValues) {
    if (changed.has('initialFilter')) this.filter = this.initialFilter === 'all' ? 'all' : 'current';
    if (changed.has('rpc') || changed.has('selected')) {++this.actionEpoch;this.busy=false;this.review=null;this.showRunForm=false;this.editingWork=false;this.detail=null;this.nextRunOffset=0;this.logs={};void this.refresh();}
  }
  private clearPoll() {if(this.timer!==undefined)clearTimeout(this.timer);this.timer=undefined;}
  private schedule() {this.clearPoll();if(this.isConnected&&!document.hidden)this.timer=setTimeout(()=>void this.refresh(),4000);}
  async refresh() {
    if(!this.rpc||!this.isConnected||document.hidden||this.busy){this.schedule();return;}
    const epoch=++this.epoch, selected=this.selected;this.loading=true;
    try {
      let result:Data;
      if(selected) {
        const count=Math.max(20,this.nextRunOffset);let offset=0,runs:Data[]=[];let page:Data;
        do {page=await this.rpc<Data>('work.get',{work_id:selected,run_offset:offset,run_limit:20});const batch=page.runs||[];runs=mergeUnique(runs,batch,run=>run.id);offset=nextPage(page,offset,batch,'has_more_runs','next_run_offset');}
        while(page.has_more_runs&&offset<count&&epoch===this.epoch&&this.isConnected);
        result={...page,runs,next_run_offset:offset};
      } else {
        const count=Math.max(200,this.nextOffset);let offset=0,works:Data[]=[];let page:Data;
        do {page=await this.rpc<Data>('work.list',{include_archived:true,limit:200,offset});const batch=page.works||[];works=mergeUnique(works,batch,workId);offset=nextPage(page,offset,batch,'has_more','next_offset');}
        while(page.has_more&&offset<count&&epoch===this.epoch&&this.isConnected);
        result={works,has_more:!!page.has_more,next_offset:offset};
      }
      if(!this.isConnected||epoch!==this.epoch)return;
      if(selected){this.detail=result;this.nextRunOffset=result.next_run_offset;if(this.review&&!(result.work?.pending_approvals||[]).some((approval:Data)=>approval.id===this.review?.id))this.review=null;}else {this.works=result.works||[];this.hasMore=!!result.has_more;this.nextOffset=Number(result.next_offset)||this.works.length;this.loaded=true;}if(!this.mutationError)this.error='';
    }catch(error){if(this.isConnected&&epoch===this.epoch)this.error=errorText(error);}
    finally{if(this.isConnected&&epoch===this.epoch){this.loading=false;this.schedule();}}
  }
  private async loadMore() {
    if(!this.rpc||this.loading||this.busy||!this.hasMore)return;
    this.clearPoll();const epoch=++this.epoch;this.loading=true;
    try {const result=await this.rpc<Data>('work.list',{include_archived:true,limit:200,offset:this.nextOffset});
      if(!this.isConnected||epoch!==this.epoch)return;
      const batch=result.works||[];const next=nextPage(result,this.nextOffset,batch,'has_more','next_offset');this.works=mergeUnique(this.works,batch,workId);this.nextOffset=next;this.hasMore=!!result.has_more;this.error='';
    } catch(error){if(this.isConnected&&epoch===this.epoch)this.error=errorText(error);}
    finally{if(this.isConnected&&epoch===this.epoch){this.loading=false;this.schedule();}}
  }
  private async loadMoreRuns() {
    if(!this.rpc||this.loading||this.busy||!this.detail?.has_more_runs||!this.selected)return;
    this.clearPoll();const epoch=++this.epoch,selected=this.selected;this.loading=true;
    try {const result=await this.rpc<Data>('work.get',{work_id:selected,run_offset:this.nextRunOffset,run_limit:20});
      if(!this.isConnected||epoch!==this.epoch||selected!==this.selected)return;
      const batch=result.runs||[];const next=nextPage(result,this.nextRunOffset,batch,'has_more_runs','next_run_offset');
      this.detail={...result,runs:mergeUnique(this.detail?.runs||[],batch,run=>run.id)};this.nextRunOffset=next;this.error='';
    } catch(error){if(this.isConnected&&epoch===this.epoch)this.error=errorText(error);}
    finally{if(this.isConnected&&epoch===this.epoch){this.loading=false;this.schedule();}}
  }
  private choose(id:string) {this.selected=id;this.notice='';this.dispatchEvent(new CustomEvent('work-selected',{detail:{work_id:id},bubbles:true}));}
  private enterWork(work:Data) {
    if (typeof work.conversation_id === 'string' && work.conversation_id) this.openConversation(work.conversation_id);
    else this.choose(workId(work));
  }
  private downloadArtifact(run:Data,path:string) {
    void this.mutate(async()=>{
      const result=await this.rpc<Data>('work.run.artifact',{run_id:run.id,path});
      if(result.encoding!=='base64'||typeof result.data!=='string')throw new Error('产物暂时无法下载，请在电脑查看。');
      const bytes=Uint8Array.from(atob(result.data),character=>character.charCodeAt(0));
      const url=URL.createObjectURL(new Blob([bytes],{type:result.mime_type||'application/octet-stream'}));
      try{const link=document.createElement('a');link.href=url;link.download=result.name||path.split(/[\\/]/).at(-1)||'result';link.click();}
      finally{setTimeout(()=>URL.revokeObjectURL(url),10000);}
    });
  }
  private async mutate(action:()=>Promise<unknown>,notice='') {
    if(this.busy)return;this.busy=true;this.error='';this.notice='';this.mutationError=false;this.clearPoll();++this.epoch;
    const epoch=this.actionEpoch;
    try{await action();if(this.isConnected&&epoch===this.actionEpoch)this.notice=notice;}
    catch(error){if(this.isConnected&&epoch===this.actionEpoch){this.error=errorText(error);this.mutationError=true;}}
    finally{if(this.isConnected&&epoch===this.actionEpoch){this.busy=false;await this.refresh();}}
  }
  private prepare(event:SubmitEvent) {
    event.preventDefault();const draft={...this.runDraft};let args:unknown;
    try{args=JSON.parse(draft.arguments||'[]');if(!Array.isArray(args)||!args.every(value=>typeof value==='string'))throw new Error();}
    catch{this.error='参数请填写 JSON 字符串数组，例如 ["--epochs", "10"]。';return;}
    const content=JSON.stringify({work_id:this.selected,...draft});
    if(content!==this.prepareRequest.content)this.prepareRequest={content,id:'ui-prepare:'+crypto.randomUUID()};
    void this.mutate(async()=>{
      const result=await this.rpc<Data>('work.run.prepare',{work_id:this.selected,script:draft.script.trim(),arguments:args,cwd:draft.cwd.trim()||'.',
        output_paths:draft.output_paths.split(/\r?\n/).map(path=>path.trim()).filter(Boolean),timeout_seconds:Number(draft.timeout),request_id:this.prepareRequest.id});
      this.prepareRequest={content:'',id:''};
      this.review=result.approval;this.showRunForm=false;
    });
  }
  private approve(approval:Data) {
    // A single explicit approval is bound to the exact immutable digest; never a blanket tool grant.
    void this.mutate(async()=>{
      const result=await this.rpc<Data>('work.run.approve',{approval_id:approval.id,expected_digest:approval.digest});
      await this.rpc('work.run.start',{approval_id:approval.id,approval_token:result.approval_token,request_id:'ui-start:'+approval.id});
      this.review=null;
    },'启动请求已提交，请以运行状态为准。');
  }
  private changeIntent(intent:string) {
    const work=this.detail?.work;if(!work)return;
    void this.mutate(()=>this.rpc('work.update',{work_id:workId(work),expected_revision:work.revision,patch:{intent}}),'工作已更新。');
  }
  private editWork(work:Data) {
    this.draft={source_kind:work.source?.kind||'none',path:work.source?.path||''};this.editingWork=true;
  }
  private saveSource(event:SubmitEvent) {
    event.preventDefault();const work=this.detail?.work,draft={...this.draft};if(!work)return;
    void this.mutate(async()=>{await this.rpc('work.update',{work_id:workId(work),expected_revision:work.revision,patch:{
      source:draft.source_kind==='none'?null:{kind:draft.source_kind,root_id:work.source?.root_id||'workspace',path:draft.path.trim()}}});this.editingWork=false;},'状态来源已更新。');
  }
  private sourceForm() {
    return html`<form class="mirally-product-form mirally-work-card" data-testid="work-source-form" @submit=${this.saveSource}>
      <h2>状态来源</h2>
      <label class="mirally-product-field">状态来源<select .value=${this.draft.source_kind} @change=${(e:Event)=>this.draft={...this.draft,source_kind:(e.target as HTMLSelectElement).value}} ?disabled=${this.busy}>
        <option value="none">暂不关联外部状态</option><option value="json">关注工作区内的 JSON 状态文件</option><option value="log">关注工作区内的日志</option>
      </select></label>
      ${this.draft.source_kind!=='none'?html`<label class="mirally-product-field">工作区内的相对文件路径<input .value=${live(this.draft.path)} required ?disabled=${this.busy} maxlength="512" @input=${(e:Event)=>this.draft={...this.draft,path:(e.target as HTMLInputElement).value}}></label><p class="mirally-product-meta">只读取已获准工作区中的文件。关注已有程序不会获得停止或重跑它的权限。</p>${this.draft.source_kind==='json'?html`<details><summary>状态文件格式</summary><pre class="mirally-work-log">${JSON.stringify({state:'running',summary:'正在训练',progress:{current:2,total:10},metrics:{loss:0.3}},null,2)}</pre><p class="mirally-product-meta">以上仅为格式示例。进度与指标必须由实际程序写入；没有数据时不推测百分比。</p></details>`:nothing}`:nothing}
      <div class="mirally-product-actions"><button type="submit" class="btn primary" ?disabled=${this.busy}>保存来源</button><button type="button" class="btn" ?disabled=${this.busy} @click=${()=>this.editingWork=false}>取消</button></div>
    </form>`;
  }
  private runForm() {
    const field=(key:keyof typeof this.runDraft,label:string)=>html`<label class="mirally-product-field">${label}<input .value=${live(this.runDraft[key])} ?required=${key==='script'} ?disabled=${this.busy} @input=${(e:Event)=>this.runDraft={...this.runDraft,[key]:(e.target as HTMLInputElement).value}}></label>`;
    return html`<form class="mirally-product-form mirally-work-card" @submit=${this.prepare} data-testid="work-run-form"><h3>准备后台运行</h3>
      <p class="mirally-product-meta">使用工作区内的脚本。准备后会展示脚本指纹和实际影响，再由你确认；脚本可以修改文件或访问网络。</p>
      ${field('script','脚本路径（工作区相对路径）')}${field('arguments','参数（JSON 字符串数组）')}${field('cwd','运行目录（工作区相对路径）')}${field('timeout','最长运行时间（秒）')}
      <label class="mirally-product-field">预期产物路径（每行一个，工作区相对路径）<textarea .value=${live(this.runDraft.output_paths)} ?disabled=${this.busy} @input=${(e:Event)=>this.runDraft={...this.runDraft,output_paths:(e.target as HTMLTextAreaElement).value}}></textarea></label>
      <div class="mirally-product-actions"><button type="submit" class="btn" ?disabled=${this.busy}>查看并确认影响</button><button type="button" class="btn" ?disabled=${this.busy} @click=${()=>this.showRunForm=false}>取消准备</button></div>
    </form>`;
  }
  private approvalCard(approval:Data) {
    const spec=approval.spec||{};
    return html`<section class="mirally-work-card mirally-product-form" data-testid="work-approval"><h3>确认这一次运行</h3>
      <dl class="mirally-work-facts"><dt>脚本</dt><dd>${spec.script}</dd><dt>SHA-256</dt><dd><code>${spec.script_sha256}</code></dd><dt>参数</dt><dd><code>${JSON.stringify(spec.arguments||[])}</code></dd><dt>运行目录</dt><dd>${spec.cwd}</dd><dt>产物</dt><dd>${(spec.output_paths||[]).join('、')||'未指定'}</dd><dt>最长运行</dt><dd>${spec.timeout_seconds} 秒</dd><dt>有效至</dt><dd>${timeLabel(approval.expires_at)}</dd></dl>
      <p class="mirally-product-meta">只批准上面这一次脚本运行。脚本并非沙箱，能够以当前电脑用户身份操作；请确认脚本内容和参数。</p>
      <button class="btn primary" data-testid="work-approve" ?disabled=${this.busy||!['pending','approved'].includes(approval.status)||Number(approval.expires_at)<Date.now()} @click=${()=>this.approve(approval)}>${approval.status==='approved'?'核对并提交这次运行':'批准并启动这次运行'}</button>
    </section>`;
  }
  private runCard(run:Data) {
    return html`<section class="mirally-work-card mirally-product-form" data-testid="work-run"><h3>${STATE[run.status]||run.status}</h3>
      <p class="mirally-product-meta">${run.id} · ${timeLabel(run.started_at||run.created_at)}${run.finished_at?html` · 结束于 ${timeLabel(run.finished_at)}`:nothing}${run.exit_code!==null&&run.exit_code!==undefined?` · 退出码 ${run.exit_code}`:''}</p>
      <p><code>${run.spec?.script||''}</code></p>
      <div class="mirally-product-actions">${['running','starting','cancelling'].includes(run.status)?html`<button class="btn" ?disabled=${this.busy||run.status==='cancelling'} @click=${()=>this.mutate(()=>this.rpc('work.run.cancel',{run_id:run.id}),'已请求停止，请等待运行器核实。')}>${run.status==='cancelling'?'正在停止…':'停止这次运行'}</button>`:nothing}
        <button class="btn" ?disabled=${this.busy} @click=${()=>this.mutate(async()=>{const result=await this.rpc<Data>('work.run.logs',{run_id:run.id,offset:0,limit:16000});this.logs={...this.logs,[run.id]:result.text||'暂无日志'};})}>查看日志</button></div>
      ${run.id in this.logs?html`<pre class="mirally-work-log" tabindex="0">${this.logs[run.id]}</pre><p class="mirally-product-meta">展示日志开头，最多 16,000 字符。</p>`:nothing}
      ${(run.artifacts||[]).length?html`<h4>产物</h4>${run.artifacts.map((artifact:Data|string)=>{const path=typeof artifact==='string'?artifact:artifact.path;return html`<div class="mirally-product-actions"><span class="mirally-product-body">${path||(typeof artifact==='string'?'结果文件':artifact.name)||'结果文件'}</span>${path?html`<button class="btn" ?disabled=${this.busy} @click=${()=>this.downloadArtifact(run,path)}>下载</button>`:nothing}</div>`;})}<p class="mirally-product-meta">小文件可直接下载（最多 2 MiB）；较大产物请在电脑查看。</p>`:nothing}
    </section>`;
  }
  private workDetail() {
    const work=this.detail?.work;if(!work)return html`<p class="mirally-product-empty">${this.loading?'正在读取工作…':'请选择工作或刷新。'}</p>`;
    const observation=work.observation, approvals=(work.pending_approvals||[]).filter((item:Data)=>['pending','approved'].includes(item.status));
    const reviews=approvals;
    return html`<div class="mirally-work-detail"><button class="btn" @click=${()=>this.choose('')}>返回工作列表</button>
      <section class="mirally-work-card mirally-product-form"><p class="mirally-product-meta">${STATE[work.status]||'状态待核对'} · 更新于 ${timeLabel(work.updated_at)}${work.observation?html` · 来源：${STATE[work.observation.state]||'状态待核对'}`:nothing}${work.latest_run?html` · 最近运行：${STATE[work.latest_run.status]||work.latest_run.status}`:nothing}</p><h2>${work.title}</h2>
        ${work.goal?html`<p class="mirally-product-body">${work.goal}</p>`:nothing}
        <div class="mirally-product-actions">${work.conversation_id?html`<button class="btn primary" @click=${()=>this.openConversation(work.conversation_id)}>继续交代 / 查看对话</button>`:nothing}
          ${work.source&&work.intent==='watching'?html`<button class="btn" ?disabled=${this.busy} @click=${()=>this.mutate(()=>this.rpc('work.observe',{work_id:workId(work)}),'已核对状态来源。')}>核对最新状态</button>`:nothing}
          ${work.intent==='watching'?html`<button class="btn" ?disabled=${this.busy} @click=${()=>this.showRunForm=!this.showRunForm}>准备运行</button><button class="btn" ?disabled=${this.busy} @click=${()=>this.changeIntent('ended')}>结束关注</button>`:html`<button class="btn" ?disabled=${this.busy} @click=${()=>this.changeIntent('watching')}>恢复关注</button>`}
          ${work.intent!=='archived'?html`<button class="btn" ?disabled=${this.busy} @click=${()=>this.changeIntent('archived')}>归档</button>`:nothing}</div>
        <button class="btn" ?disabled=${this.busy} @click=${()=>this.editWork(work)}>设置状态来源</button>
        <p class="mirally-product-meta">结束关注或归档不等于停止程序。运行完成表示执行已结束，目标与成果仍需核验。</p>
        <details><summary>这项工作的通知</summary><div class="mirally-product-actions">${[['completed','运行完成'],['failed','异常'],['approval','需要确认']].map(([key,label])=>html`<label><input type="checkbox" .checked=${work.notifications?.[key]!==false} ?disabled=${this.busy} @change=${(e:Event)=>this.mutate(()=>this.rpc('work.update',{work_id:workId(work),expected_revision:work.revision,patch:{notifications:{...work.notifications,[key]:(e.target as HTMLInputElement).checked}}}))}>${label}</label>`)}</div></details>
      </section>
      ${observation?html`<section class="mirally-work-card mirally-product-form"><h3>最近观测</h3><p class="mirally-product-meta">${timeLabel(observation.observed_at||observation.updated_at)} · ${work.source?.path||''}</p><pre class="mirally-work-log">${observation.summary||observation.text||'来源暂未提供摘要'}</pre>${observation.progress?html`<p>${observation.progress.current} / ${observation.progress.total}</p>`:nothing}${observation.metrics?html`<dl class="mirally-work-facts">${Object.entries(observation.metrics).map(([key,value])=>html`<dt>${key}</dt><dd>${value}</dd>`)}</dl>`:nothing}</section>`:work.source?html`<p class="mirally-product-meta">尚未读取状态来源。可核对状态后查看事实，不推测完成百分比。</p>`:nothing}
      ${this.editingWork?this.sourceForm():nothing}${this.showRunForm?this.runForm():nothing}${reviews.map((review:Data)=>this.approvalCard(review))}
      ${(this.detail?.runs||[]).map((run:Data)=>this.runCard(run))}
      ${this.detail?.has_more_runs?html`<button class="btn" ?disabled=${this.loading||this.busy} @click=${()=>this.loadMoreRuns()}>${this.loading?'正在加载…':'加载更多运行记录'}</button>`:nothing}
    </div>`;
  }
  private desktopList() {
    const historic=(work:Data)=>['ended','archived'].includes(work.intent);
    const counts={current:this.works.filter(work=>!historic(work)).length, attention:this.works.filter(work=>!historic(work)&&needsAttention(work)).length, history:this.works.filter(historic).length, all:this.works.length};
    const query=this.query.trim().toLocaleLowerCase();
    const filtered=this.works.filter(work=>{
      if(this.filter==='current'&&historic(work))return false;
      if(this.filter==='attention'&&(historic(work)||!needsAttention(work)))return false;
      if(this.filter==='history'&&!historic(work))return false;
      return !query||[work.title,work.goal,work.observation?.summary,primaryStatus(work),sourceStatus(work),runStatus(work),(work.pending_approvals||[]).length?'有待确认的运行':'',work.conversation_activity==='running'?'关联对话正在回复':''].some(value=>String(value||'').toLocaleLowerCase().includes(query));
    });
    const works=sortWorks(filtered,this.desktopSort,true);
    const filters:[string,string,number][]=[['current','关注中',counts.current],['attention','需要处理',counts.attention],['history','已结束 / 归档',counts.history],['all','全部',counts.all]];
    return html`<div class="mirally-work-overview">
      <div class="mirally-work-toolbar"><div><h2>工作对话</h2></div>
        <div class="mirally-product-actions">${this.openPreparation?html`<button class="btn primary" @click=${()=>this.openPreparation?.()}>开始工作</button>`:nothing}<button class="btn" ?disabled=${this.busy||this.loading} @click=${()=>this.refresh()}>刷新</button></div></div>
      <div class="mirally-work-controls"><nav class="mirally-work-filters" aria-label="筛选工作">${filters.map(([id,label,count])=>html`<button type="button" class="mirally-work-filter" aria-pressed=${this.filter===id?'true':'false'} @click=${()=>this.filter=id}>${label} <span>${count}</span></button>`)}</nav>
        <div class="mirally-work-search"><label><span class="mirally-work-visually-hidden">搜索工作</span><input type="search" placeholder="搜索名称、目标或状态" .value=${live(this.query)} @input=${(event:Event)=>this.query=(event.target as HTMLInputElement).value}></label><label><span class="mirally-work-visually-hidden">排序方式</span><select .value=${this.desktopSort} @change=${(event:Event)=>this.desktopSort=(event.target as HTMLSelectElement).value}><option value="attention">优先处理</option><option value="updated">最近更新</option></select></label></div></div>
      ${this.hasMore?html`<p class="mirally-product-meta">搜索、筛选和统计仅针对已加载的 ${this.works.length} 项工作。</p>`:nothing}
      ${!works.length?html`<div class="mirally-work-empty"><strong>${!this.loaded&&this.loading?'正在读取工作…':!this.loaded&&this.error?'暂时无法读取工作':this.works.length?'没有符合条件的工作':'还没有工作'}</strong><p class="mirally-product-meta">${!this.loaded&&this.loading?'请稍候。':!this.loaded&&this.error?'请检查连接后重试。':this.works.length?'试试其他筛选条件或搜索词。':'在对话中让 Mirally 记录并跟进工作。'}</p></div>`:html`<div class="mirally-work-list mirally-work-list-desktop">${works.map(work=>html`<article class="mirally-work-card mirally-work-row"><button class="mirally-work-row-main" data-testid="work-item" @click=${()=>this.enterWork(work)}><span class="mirally-work-row-head"><strong>${work.title}</strong><span class="mirally-work-status" data-status=${work.status}>${primaryStatus(work)}</span></span>${work.observation?.summary?html`<span class="mirally-work-row-summary">${work.observation.summary}</span>`:work.goal?html`<span class="mirally-work-row-summary">${work.goal}</span>`:nothing}<span class="mirally-product-meta">${work.observation?'来源：'+sourceStatus(work)+' · 观测于 '+timeLabel(work.observation.observed_at)+' · ':''}${progressLabel(work)?'进度 '+progressLabel(work)+' · ':''}${work.latest_run?'最近运行：'+runStatus(work)+' · ':''}${work.conversation_activity==='running'?'关联对话正在回复 · ':''}更新于 ${timeLabel(work.updated_at)}${(work.pending_approvals||[]).length?html` · 有待确认的运行`:nothing}</span></button><div class="mirally-work-row-actions"><button class="btn" aria-label=${'查看工作详情：'+work.title} @click=${()=>this.choose(workId(work))}>详情</button></div></article>`)}</div>`}
      ${this.hasMore?html`<div class="mirally-product-actions"><button class="btn" ?disabled=${this.loading||this.busy} @click=${()=>this.loadMore()}>${this.loading?'正在加载…':'加载更多工作'}</button></div>`:nothing}
    </div>`;
  }
  override render() {
    const works=sortWorks(this.works,this.sort,this.showCompleted);
    return html`<div class="mirally-work-panel" aria-busy=${this.busy||this.loading?'true':'false'}>
      ${this.error?html`<p role="alert" class="callout danger">${this.error}</p>`:nothing}${this.notice?html`<p role="status" class="callout">${this.notice}</p>`:nothing}
      ${this.selected?this.workDetail():this.desktop?this.desktopList():html`<div class="mirally-product-actions"><button class="btn" ?disabled=${this.busy||this.loading} @click=${()=>this.refresh()}>刷新</button></div>
        ${!works.length?html`<p class="mirally-product-empty">${this.loading&&!this.loaded?'正在读取工作…':this.works.length?'已加载的工作中没有符合显示设置的项目。':'还没有工作。在对话中让 Mirally 记录并跟进工作。'}</p>`:html`<div class="mirally-work-list">${works.map(work=>html`<article class="mirally-work-card mirally-work-row"><button class="mirally-work-row-main" data-testid="work-item" @click=${()=>this.enterWork(work)}><span class="mirally-work-row-head"><strong>${work.title}</strong><span class="mirally-work-status" data-status=${work.status}>${primaryStatus(work)}</span></span>${work.observation?.summary||work.goal?html`<span class="mirally-work-row-summary">${work.observation?.summary||work.goal}</span>`:nothing}<small class="mirally-product-meta">${timeLabel(work.updated_at)}</small></button><div class="mirally-work-row-actions"><button class="btn" aria-label=${'查看工作详情：'+work.title} @click=${()=>this.choose(workId(work))}>详情</button></div></article>`)}</div>`}
        ${this.hasMore?html`<div class="mirally-work-mobile-more"><p class="mirally-product-meta">当前显示设置仅应用于已加载的 ${this.works.length} 项工作。</p><button class="btn" ?disabled=${this.loading||this.busy} @click=${()=>this.loadMore()}>${this.loading?'正在加载…':'加载更多工作'}</button></div>`:nothing}`}
    </div>`;
  }
}
