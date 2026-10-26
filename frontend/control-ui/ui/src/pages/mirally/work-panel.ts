import {html, nothing, type PropertyValues} from 'lit';
import {customElement, property, state} from 'lit/decorators.js';
import {live} from 'lit/directives/live.js';
import {OpenClawLightDomElement} from '../../lit/openclaw-element.ts';
import {timeLabel, type Rpc} from '../../mobile/api.ts';
import './product.css';

type Data = Record<string, any>;
const STATE: Record<string, string> = {idle:'待开始',observing:'关注中',needs_approval:'需要确认',starting:'正在启动',running:'正在运行',cancelling:'正在停止',completed:'运行完成',failed:'运行失败',unknown:'需要核对',ended:'已结束关注',archived:'已归档',cancelled:'已停止',interrupted:'运行中断',blocked:'等待处理',timed_out:'运行超时'};
const workId = (work: Data) => work.id || work.work_id;
const ATTENTION:Record<string,number>={needs_approval:0,failed:1,unknown:2,cancelling:3,running:4,observing:5,idle:6};
const attention = (work: Data) => ATTENTION[work.status] ?? 7;
export function sortWorks(works: Data[], sort: string, showCompleted: boolean) {
  return works.filter(work => showCompleted || !['completed','ended','archived'].includes(work.status)).sort((a,b) =>
    (sort === 'attention' ? attention(a) - attention(b) : 0) || Number(b.updated_at || 0) - Number(a.updated_at || 0));
}
const errorText = (error: unknown) => error instanceof Error || (error && typeof (error as Data).message==='string') ? (error as Error).message : '操作未完成，请刷新真实状态。';

/** Shared real work surface. Neither the phone nor this component owns execution state. */
@customElement('mirally-work-panel')
export class MirallyWorkPanel extends OpenClawLightDomElement {
  @property({attribute:false}) rpc!: Rpc;
  @property({attribute:false}) openConversation: (id:string) => void = () => {};
  @property() sort = 'attention';
  @property({type:Boolean}) showCompleted = true;
  @property() selected = '';
  @state() private works: Data[] = [];
  @state() private detail: Data | null = null;
  @state() private busy = false;
  @state() private loading = false;
  @state() private error = '';
  @state() private notice = '';
  @state() private creating = false;
  @state() private editingWork = false;
  @state() private showRunForm = false;
  @state() private draft = {title:'',goal:'',source_kind:'none',path:''};
  @state() private runDraft = {script:'',arguments:'',cwd:'.',output_paths:'',timeout:'300'};
  @state() private review: Data | null = null;
  @state() private logs: Record<string,string> = {};
  private timer: ReturnType<typeof setTimeout> | undefined;
  private epoch = 0;
  private actionEpoch = 0;
  private createRequest = {content:'',id:''};
  private prepareRequest = {content:'',id:''};
  private mutationError = false;
  private readonly visibilityChanged = () => {this.clearPoll(); if (!document.hidden) void this.refresh();};
  override connectedCallback() {super.connectedCallback();document.addEventListener('visibilitychange',this.visibilityChanged);}
  override disconnectedCallback() {++this.epoch;++this.actionEpoch;this.clearPoll();document.removeEventListener('visibilitychange',this.visibilityChanged);super.disconnectedCallback();}
  protected override updated(changed: PropertyValues) {
    if (changed.has('rpc') || changed.has('selected')) {++this.actionEpoch;this.busy=false;this.review=null;this.showRunForm=false;this.editingWork=false;this.detail=null;void this.refresh();}
  }
  private clearPoll() {if(this.timer!==undefined)clearTimeout(this.timer);this.timer=undefined;}
  private schedule() {this.clearPoll();if(this.isConnected&&!document.hidden)this.timer=setTimeout(()=>void this.refresh(),4000);}
  async refresh() {
    if(!this.rpc||!this.isConnected||document.hidden||this.busy){this.schedule();return;}
    const epoch=++this.epoch, selected=this.selected;this.loading=true;
    try {
      const result = selected ? await this.rpc<Data>('work.get',{work_id:selected}) : await this.rpc<Data>('work.list',{include_archived:true,limit:200});
      if(!this.isConnected||epoch!==this.epoch)return;
      if(selected){this.detail=result;if(this.review&&(result.runs||[]).some((run:Data)=>run.approval_id===this.review?.id))this.review=null;}else this.works=result.works||[];if(!this.mutationError)this.error='';
    }catch(error){if(this.isConnected&&epoch===this.epoch)this.error=errorText(error);}
    finally{if(this.isConnected&&epoch===this.epoch){this.loading=false;this.schedule();}}
  }
  private choose(id:string) {this.selected=id;this.notice='';this.dispatchEvent(new CustomEvent('work-selected',{detail:{work_id:id},bubbles:true}));}
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
  private create(event:SubmitEvent) {
    event.preventDefault();const draft={...this.draft};
    if(!draft.title.trim())return;
    const content=JSON.stringify(draft);
    if(content!==this.createRequest.content)this.createRequest={content,id:'ui-work:'+crypto.randomUUID()};
    void this.mutate(async()=>{
      const result=await this.rpc<Data>('work.create',{title:draft.title.trim(),goal:draft.goal.trim(),
        request_id:this.createRequest.id,...(draft.source_kind==='none'?{}:{source:{kind:draft.source_kind,root_id:'workspace',path:draft.path.trim()}})});
      this.createRequest={content:'',id:''};
      this.creating=false;this.draft={title:'',goal:'',source_kind:'none',path:''};this.choose(workId(result.work));
    },'工作已建立。');
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
    this.draft={title:work.title||'',goal:work.goal||'',source_kind:work.source?.kind||'none',path:work.source?.path||''};this.editingWork=true;
  }
  private saveWork(event:SubmitEvent) {
    event.preventDefault();const work=this.detail?.work,draft={...this.draft};if(!work)return;
    void this.mutate(async()=>{await this.rpc('work.update',{work_id:workId(work),expected_revision:work.revision,patch:{title:draft.title.trim(),goal:draft.goal.trim(),
      source:draft.source_kind==='none'?null:{kind:draft.source_kind,root_id:work.source?.root_id||'workspace',path:draft.path.trim()}}});this.editingWork=false;},'工作与来源已更新。');
  }
  private createForm(editing=false) {
    const field=(key:keyof typeof this.draft,label:string,required=false)=>html`<label class="mirally-product-field">${label}<input .value=${live(this.draft[key])} ?required=${required} ?disabled=${this.busy} maxlength=${key==='goal'?4000:512} @input=${(e:Event)=>this.draft={...this.draft,[key]:(e.target as HTMLInputElement).value}}></label>`;
    return html`<form class="mirally-product-form mirally-work-card" data-testid="work-create-form" @submit=${editing?this.saveWork:this.create}>
      <h2>${editing?'工作与状态来源':'交代一件工作'}</h2>${field('title','工作名称',true)}${field('goal','希望得到什么')}
      <label class="mirally-product-field">状态来源<select .value=${this.draft.source_kind} @change=${(e:Event)=>this.draft={...this.draft,source_kind:(e.target as HTMLSelectElement).value}} ?disabled=${this.busy}>
        <option value="none">暂不关联外部状态</option><option value="json">关注工作区内的 JSON 状态文件</option><option value="log">关注工作区内的日志</option>
      </select></label>
      ${this.draft.source_kind!=='none'?html`${field('path','工作区内的相对文件路径',true)}<p class="mirally-product-meta">只读取已获准工作区中的文件。关注已有程序不会获得停止或重跑它的权限。</p>${this.draft.source_kind==='json'?html`<details><summary>状态文件格式</summary><pre class="mirally-work-log">${JSON.stringify({state:'running',summary:'正在训练',progress:{current:2,total:10},metrics:{loss:0.3}},null,2)}</pre><p class="mirally-product-meta">以上仅为格式示例。进度与指标必须由实际程序写入；没有数据时不推测百分比。</p></details>`:nothing}`:nothing}
      <div class="mirally-product-actions"><button type="submit" class="btn primary" ?disabled=${this.busy}>${editing?'保存工作':'建立工作'}</button><button type="button" class="btn" ?disabled=${this.busy} @click=${()=>{this.creating=false;this.editingWork=false;}}>${editing?'取消修改':'返回列表'}</button></div>
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
    const review=this.review||approvals[0];
    return html`<div class="mirally-work-detail"><button class="btn" @click=${()=>this.choose('')}>返回工作列表</button>
      <section class="mirally-work-card mirally-product-form"><p class="mirally-product-meta">${STATE[work.status]||'状态待核对'} · 更新于 ${timeLabel(work.updated_at)}</p><h2>${work.title}</h2>
        ${work.goal?html`<p class="mirally-product-body">${work.goal}</p>`:nothing}
        <div class="mirally-product-actions">${work.conversation_id?html`<button class="btn primary" @click=${()=>this.openConversation(work.conversation_id)}>继续交代 / 查看对话</button>`:nothing}
          ${work.source&&work.intent==='watching'?html`<button class="btn" ?disabled=${this.busy} @click=${()=>this.mutate(()=>this.rpc('work.observe',{work_id:workId(work)}),'已核对状态来源。')}>核对最新状态</button>`:nothing}
          ${work.intent==='watching'?html`<button class="btn" ?disabled=${this.busy} @click=${()=>this.showRunForm=!this.showRunForm}>准备运行</button><button class="btn" ?disabled=${this.busy} @click=${()=>this.changeIntent('ended')}>结束关注</button>`:html`<button class="btn" ?disabled=${this.busy} @click=${()=>this.changeIntent('watching')}>恢复关注</button>`}
          ${work.intent!=='archived'?html`<button class="btn" ?disabled=${this.busy} @click=${()=>this.changeIntent('archived')}>归档</button>`:nothing}</div>
        <button class="btn" ?disabled=${this.busy} @click=${()=>this.editWork(work)}>工作与状态来源设置</button>
        <p class="mirally-product-meta">结束关注或归档不等于停止程序。运行完成表示执行已结束，目标与成果仍需核验。</p>
        <details><summary>这项工作的通知</summary><div class="mirally-product-actions">${[['completed','运行完成'],['failed','异常'],['approval','需要确认']].map(([key,label])=>html`<label><input type="checkbox" .checked=${work.notifications?.[key]!==false} ?disabled=${this.busy} @change=${(e:Event)=>this.mutate(()=>this.rpc('work.update',{work_id:workId(work),expected_revision:work.revision,patch:{notifications:{...work.notifications,[key]:(e.target as HTMLInputElement).checked}}}))}>${label}</label>`)}</div></details>
      </section>
      ${observation?html`<section class="mirally-work-card mirally-product-form"><h3>最近观测</h3><p class="mirally-product-meta">${timeLabel(observation.observed_at||observation.updated_at)} · ${work.source?.path||''}</p><pre class="mirally-work-log">${observation.summary||observation.text||'来源暂未提供摘要'}</pre>${observation.progress?html`<p>${observation.progress.current} / ${observation.progress.total}</p>`:nothing}${observation.metrics?html`<dl class="mirally-work-facts">${Object.entries(observation.metrics).map(([key,value])=>html`<dt>${key}</dt><dd>${value}</dd>`)}</dl>`:nothing}</section>`:work.source?html`<p class="mirally-product-meta">尚未读取状态来源。可核对状态后查看事实，不推测完成百分比。</p>`:nothing}
      ${this.editingWork?this.createForm(true):nothing}${this.showRunForm?this.runForm():nothing}${review?this.approvalCard(review):nothing}
      ${(this.detail?.runs||[]).map((run:Data)=>this.runCard(run))}
    </div>`;
  }
  override render() {
    const works=sortWorks(this.works,this.sort,this.showCompleted);
    return html`<div class="mirally-work-panel" aria-busy=${this.busy||this.loading?'true':'false'}>
      ${this.error?html`<p role="alert" class="callout danger">${this.error}</p>`:nothing}${this.notice?html`<p role="status" class="callout">${this.notice}</p>`:nothing}
      ${this.selected?this.workDetail():this.creating?this.createForm():html`<div class="mirally-product-actions"><button class="btn primary" data-testid="work-create" @click=${()=>this.creating=true}>交代 / 关注工作</button><button class="btn" ?disabled=${this.busy||this.loading} @click=${()=>this.refresh()}>刷新</button></div>
        ${!works.length?html`<p class="mirally-product-empty">${this.loading?'正在读取工作…':'还没有工作。可以交代一件事，或关注工作区里的实验状态。'}</p>`:html`<div class="mirally-work-list">${works.map(work=>html`<button class="mirally-work-card mirally-work-list-item" data-testid="work-item" @click=${()=>this.choose(workId(work))}><span class="mirally-work-status" data-status=${work.status}>${STATE[work.status]||'状态待核对'}</span><strong>${work.title}</strong><span class="mirally-product-meta">${work.goal||work.observation?.summary||'打开查看状态与成果'}</span><small class="mirally-product-meta">${timeLabel(work.updated_at)}</small></button>`)}</div>`}`}
    </div>`;
  }
}
