/* Read-only activity memory contribution within the native Memory workspace. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object' && module.exports)module.exports=api;
  else if(root.top===root && root.location.origin===__MIRALLY_ORIGIN__ && root.chrome?.webview)api.install(root);
})(typeof window==='object'?window:globalThis,function(){
  'use strict';
  const PROVENANCES={observed:'规则观测记录',model_inference:'模型整理',user_confirmed:'用户确认'};
  const KINDS={episodic:'活动经历',semantic:'事实与线索',working:'近期工作'};
  const text=value=>value==null?'':String(value);
  const owner=node=>node?.localName==='openclaw-memory-settings'?node.selectedAgentId:node?.agentId;
  function sourceLabel(row){return row?.provenance==='model_inference' ? row.kind==='episodic'?'模型活动总结':'模型推测' : PROVENANCES[row?.provenance] || '来源未标注';}
  function title(row){return {'Observed desktop context':'桌面活动观测','Activity synthesis':'活动记忆总结','Unconfirmed Activity inference':'待核对的活动推测'}[row?.title] || text(row?.title) || '活动记忆';}
  function timestamp(value){if(!Number.isFinite(value))return '';const date=new Date(value);return Number.isFinite(date.getTime())?date.toLocaleString():'';}
  function period(row){const start=timestamp(row?.observed_start_utc_ms),end=timestamp(row?.observed_end_utc_ms);return start?`${start}${end && end!==start?' — '+end:''}（本地时间）`:'观测时间未记录';}
  function filters(values){
    const result={query:text(values.query).trim(),include_candidates:values.candidates===true,limit:20,offset:0};
    if(result.query.length>512)throw Error('关键词最多 512 个字符。');
    const sources={summaries:['model_inference','user_confirmed'],observed:['observed'],model_inference:['model_inference'],user_confirmed:['user_confirmed'],all:[]};
    if(!Object.hasOwn(sources,values.source))throw Error('请选择有效的记忆来源。');
    if(sources[values.source].length)result.provenances=sources[values.source];
    if(values.kind){if(!Object.hasOwn(KINDS,values.kind))throw Error('请选择有效的记忆类型。');result.kinds=[values.kind];}
    for(const [field,target] of [['start','start_utc_ms'],['end','end_utc_ms']])if(values[field]){
      if(!/^\d{4}-\d\d-\d\d$/.test(values[field]))throw Error('请选择有效日期。');
      const parts=values[field].split('-').map(Number),date=new Date(parts[0],parts[1]-1,parts[2]);
      if(date.getFullYear()!==parts[0] || date.getMonth()!==parts[1]-1 || date.getDate()!==parts[2])throw Error('请选择有效日期。');
      if(field==='end')date.setDate(date.getDate()+1);
      result[target]=date.getTime()-(field==='end'?1:0);
      if(result[target]<0)throw Error('日期须在 1970 年之后。');
    }
    if(result.start_utc_ms!==undefined && result.end_utc_ms!==undefined && result.start_utc_ms>=result.end_utc_ms)throw Error('结束日期不能早于开始日期。');
    return result;
  }
  function retention(row){const state=row?.metadata?.source_retention_status || row?.source_retention_status;return {archived:'原始证据已清理，保留来源编号',partially_archived:'部分原始证据已清理，保留来源编号',archiving:'原始证据正在清理，保留来源编号'}[state] || (row?.metadata?.references_unavailable?'原始证据不可用，保留来源编号':'原始证据状态未单独确认');}
  function install(root){
    if(root.__mirallyMemory)return root.__mirallyMemory;
    const document=root.document;
    let panel=null,anchor=null,context=null,client=null,bridge=null,stopRouter=null,frame=null,epoch=0,searchEpoch=0,readEpoch=0;
    let query=null,nextOffset=null,currentRead=null,searching=false,reading=false;
    const $=selector=>panel?.querySelector(selector),field=name=>$(`[data-memory-filter="${name}"]`);
    const set=(name,value)=>{const node=$(`[data-memory-slot="${name}"]`);if(node)node.textContent=text(value);};
    const appContext=()=>document.querySelector('openclaw-app')?.context;
    const gateway=value=>value?.gateway?.snapshot?.client || value?.runtimeConfig?.state?.client;
    function current(token){return token===epoch && panel?.isConnected && anchor?.isConnected && owner(anchor)==='main' && appContext()===context && gateway(context)===client && root.__mirallyActivity?.memoryBrowser===bridge;}
    function buttons(){if(!panel)return;for(const node of panel.querySelectorAll('button'))node.disabled=searching || reading;}
    function clear(){epoch++;searchEpoch++;readEpoch++;bridge?.cancel?.();panel?.remove();panel=null;anchor=null;query=null;nextOffset=null;currentRead=null;searching=reading=false;}
    function values(){return {query:field('query').value,source:field('source').value,kind:field('kind').value,candidates:field('candidates').checked,start:field('start').value,end:field('end').value};}
    function meta(row){return `${sourceLabel(row)} · ${KINDS[row.kind] || '活动记忆'} · ${row.status==='candidate'?'待审核':row.status==='active'?'已保存':'状态未确认'}`;}
    function renderRows(rows){
      const list=$('[data-memory-slot="list"]');list.replaceChildren();
      for(const row of rows){
        if(!row || typeof row.id!=='string')continue;
        const button=document.createElement('button');button.type='button';button.className='mirally-memory__result';
        for(const [tag,value] of [['strong',title(row)],['span',meta(row)],['span',period(row)],['p',text(row.content)]]){const node=document.createElement(tag);node.textContent=value;button.append(node);}
        button.addEventListener('click',()=>read(row.id));list.append(button);
      }
    }
    async function search(more=false){
      const token=epoch;if(!current(token) || searching)return;
      let selected;try{selected=more?query:filters(values());if(!selected || more && !Number.isInteger(nextOffset))return;}catch(error){set('notice',error.message);return;}
      query=selected;const request=++searchEpoch;readEpoch++;reading=false;currentRead=null;searching=true;buttons();
      $('[data-memory-slot="detail"]').hidden=true;set('notice','正在读取本机记忆…');
      try{
        const result=await bridge.search({...selected,offset:more?nextOffset:0});if(!current(token) || request!==searchEpoch)return;
        const rows=Array.isArray(result?.memories)?result.memories:[];renderRows(rows);
        nextOffset=Number.isInteger(result.next_offset)?result.next_offset:null;
        $('[data-memory-action="more"]').hidden=nextOffset===null;
        const state=result.status;
        set('notice',rows.length?`${rows.length} 条记忆${nextOffset!==null?'；可继续查看下一页':result.candidate_selection_bounded?'；关键词检索仅展示有限相关结果，可缩小日期或关键词':''}`:state==='disabled'?'活动记忆已关闭，请在活动与记忆设置开启。':state==='not_ready'?'记忆尚未准备好，请检查活动与记忆后台状态。':selected.provenances?.includes('observed') || !selected.provenances?'未找到符合条件的记录；可调整关键词、来源或日期。':'当前筛选下未找到模型总结或用户确认记忆。这不表示没有历史记录；可切换所有来源或规则观测记录查看，并在活动与记忆设置查看整理进度。');
        $('[data-memory-action="observed"]').hidden=rows.length>0 || selected.provenances?.includes('observed') || !selected.provenances;
      }catch(error){if(current(token) && request===searchEpoch)set('notice',`记忆读取失败：${error.message}`);}
      finally{if(current(token) && request===searchEpoch){searching=false;buttons();}}
    }
    async function read(id,fromLine=1){
      const token=epoch;if(!current(token) || reading || typeof id!=='string' || !id)return;
      const request=++readEpoch;reading=true;buttons();set('notice','正在读取记忆详情…');
      try{
        const result=await bridge.read({memory_id:id,from_line:fromLine,line_count:100});if(!current(token) || request!==readEpoch)return;
        const row=result.memory;
        if(!row){$('[data-memory-slot="detail"]').hidden=true;set('notice','这条记忆已不可用，请刷新列表。');return;}
        currentRead={id,next:fromLine+(result.line_count || 100)};
        const detail=$('[data-memory-slot="detail"]');detail.hidden=false;
        set('title',title(row));set('meta',meta(row));set('time',period(row));set('body',result.text || '暂无正文。');
        set('explanation',row.provenance==='observed'?'这是规则整理的桌面观测记录，不是模型总结，也不证明阅读、发送或任务完成。':row.provenance==='model_inference'?'这是模型基于观测内容整理的总结或推测；已保存不等于用户确认。':row.provenance==='user_confirmed'?'此记忆标记为用户确认。':'此记录未标注来源，不能据此判断是否由模型生成。');
        const refs=Array.isArray(row.source_observation_ids)?row.source_observation_ids:[];
        set('source',`${retention(row)}${refs.length?'；来源编号：'+refs.join('、'):''}${row.references_truncated?'（编号仅显示部分）':''}`);
        set('range',`正文第 ${result.from_line || fromLine} 行起${result.truncated?'，可继续读取后续正文':''}`);
        $('[data-memory-action="detail-more"]').hidden=!result.truncated;
        const trace=$('[data-memory-slot="trace"]');trace.replaceChildren();
        const parent=row.metadata?.derived_from_memory_id;
        if(typeof parent==='string' && parent && parent!==id){const button=document.createElement('button');button.type='button';button.textContent='查看这条总结的来源观测记录';button.addEventListener('click',()=>read(parent));trace.append(button);}
        set('notice','已读取记忆详情（仅本地读取，不调用模型）');
      }catch(error){if(current(token) && request===readEpoch)set('notice',`记忆详情读取失败：${error.message}`);}
      finally{if(current(token) && request===readEpoch){reading=false;buttons();}}
    }
    function create(){
      panel=document.createElement('section');panel.id='mirally-memory-browser';panel.className='mirally-memory';panel.setAttribute('aria-label','本机活动记忆');
      panel.innerHTML='<header><div><h2>本机活动记忆</h2><p>查看已整理的记忆和来源。默认显示全部已保存记忆，来源分别标注。回答历史问题时可检索这些记忆；查看详情不会调用模型。</p></div><button type="button" data-memory-action="refresh">刷新</button></header>'+
        '<form data-memory-slot="filters"><div class="mirally-memory__filters"><label>关键词<input type="search" maxlength="512" data-memory-filter="query" placeholder="留空查看近期记忆"></label><label>来源<select data-memory-filter="source"><option value="all">所有来源</option><option value="summaries">模型整理与用户确认</option><option value="model_inference">仅模型整理</option><option value="user_confirmed">仅用户确认</option><option value="observed">规则观测记录</option></select></label><label>类型<select data-memory-filter="kind"><option value="">所有类型</option><option value="episodic">活动经历</option><option value="semantic">事实与线索</option><option value="working">近期工作</option></select></label><label>开始日期（本地）<input type="date" data-memory-filter="start"></label><label>结束日期（本地）<input type="date" data-memory-filter="end"></label></div><div class="mirally-memory__actions"><label><input type="checkbox" data-memory-filter="candidates">包含待审核记忆</label><button type="submit">查找</button><a data-memory-settings>活动与记忆设置</a></div></form>'+
        '<p role="status" aria-live="polite" data-memory-slot="notice"></p><button type="button" data-memory-action="observed" hidden>查看规则观测记录</button><div class="mirally-memory__list" data-memory-slot="list"></div><button type="button" data-memory-action="more" hidden>查看下一页</button>'+
        '<section class="mirally-memory__detail" data-memory-slot="detail" hidden><h3 data-memory-slot="title"></h3><p data-memory-slot="meta"></p><p data-memory-slot="time"></p><p data-memory-slot="explanation"></p><pre data-memory-slot="body"></pre><p data-memory-slot="source"></p><div data-memory-slot="trace"></div><p data-memory-slot="range"></p><button type="button" data-memory-action="detail-more" hidden>查看后续正文</button></section>';
      field('source').value='all';field('kind').value='';
      $('[data-memory-slot="filters"]').addEventListener('submit',event=>{event.preventDefault();search();});
      $('[data-memory-action="refresh"]').addEventListener('click',()=>search());
      $('[data-memory-action="more"]').addEventListener('click',()=>search(true));
      $('[data-memory-action="observed"]').addEventListener('click',()=>{field('source').value='observed';search();});
      $('[data-memory-action="detail-more"]').addEventListener('click',()=>currentRead && read(currentRead.id,currentRead.next));
      const link=panel.querySelector('[data-memory-settings]');link.href=root.__mirallyLegacyHost?`${context?.basePath || ''}/activity`:`${context?.basePath || ''}/settings/appearance?mirally=activity`;
      link.addEventListener('click',event=>{if(event.button!==0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)return;if(context?.navigate){event.preventDefault();if(root.__mirallyLegacyHost)context.navigate('activity',{search:''});else context.navigate('appearance',{search:'?mirally=activity'});}});
    }
    function sync(){
      frame=null;const next=appContext(),nextClient=gateway(next),nextBridge=root.__mirallyActivity?.memoryBrowser;
      if(next!==context || nextClient!==client || nextBridge!==bridge){clear();stopRouter?.();context=next;client=nextClient;bridge=nextBridge;stopRouter=next?.router?.subscribe?.(schedule);}
      const target=[...document.querySelectorAll('[data-mirally-memory-anchor], openclaw-memory-settings, openclaw-memory-memories, openclaw-agent-memory-panel')].find(node=>node.isConnected && owner(node)==='main' && !node.closest('[hidden], [inert], [aria-hidden="true"]'));
      const embedded=target?.hasAttribute('data-mirally-memory-anchor') || target?.localName==='openclaw-memory-settings';
      const container=embedded?target.querySelector('.memory-page__panel'):target?.parentNode;
      if(!target || !container || !bridge || !client){if(panel)clear();return;}
      const before=embedded?[...container.childNodes].find(node=>node!==panel) || null:target;
      if(target!==anchor){clear();anchor=target;create();container.insertBefore(panel,before);search();}
      else if(panel.parentNode!==container || panel.nextSibling!==before)container.insertBefore(panel,before);
    }
    function schedule(){if(frame===null)frame=root.requestAnimationFrame(sync);}
    const observer=new root.MutationObserver(schedule);observer.observe(document,{childList:true,subtree:true,attributes:true,attributeFilter:['hidden','inert','aria-hidden']});
    root.addEventListener('popstate',schedule);
    function dispose(){observer.disconnect();stopRouter?.();if(frame!==null)root.cancelAnimationFrame(frame);clear();}
    root.addEventListener('pagehide',dispose);
    root.__mirallyMemory={sync:schedule,refresh:()=>search(),dispose};schedule();return root.__mirallyMemory;
  }
  return {PROVENANCES,KINDS,sourceLabel,title,period,filters,retention,install};
});
