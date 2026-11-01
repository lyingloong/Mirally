const {test}=require('node:test');
const assert=require('node:assert/strict');
const {parseHTML}=require('../tools/testing/dependencies.cjs').loadLinkedom();
const memory=require('../mirally/assets/control-ui-memory.js');

function fixture(tag='openclaw-memory-settings',agent='main'){
  const content=tag==='openclaw-memory-settings'?'<section class="memory-page"><header id="native-heading">Memory title and tabs</header><div class="memory-page__panel"><div id="native">Native memory stays</div></div></section>':'<div id="native">Native memory stays</div>';
  const {window}=parseHTML(`<html><body><openclaw-app></openclaw-app><main><${tag}>${content}</${tag}></main></body></html>`);
  const proto=Object.getPrototypeOf(window.document.createElement('select'));
  if(!Object.getOwnPropertyDescriptor(proto,'value')?.set)Object.defineProperty(proto,'value',{configurable:true,get(){return this._value ?? this.querySelector('option')?.value ?? '';},set(value){this._value=value;}});
  const native=window.document.querySelector(tag);if(tag==='openclaw-memory-settings')native.selectedAgentId=agent;else native.agentId=agent;
  const requests=[],frames=new Map(),events=new Map(),pending=new Set();let sequence=0,cancels=0;
  const bridge={search:args=>request('search',args),read:args=>request('read',args),cancel(){cancels++;for(const item of pending)item.reject(Error('cancelled'));pending.clear();}};
  function request(action,args){return new Promise((resolve,reject)=>{const item={action,args,resolve:value=>{pending.delete(item);resolve(value);},reject};requests.push(item);pending.add(item);});}
  const context={gateway:{snapshot:{client:{}}},router:{subscribe:()=>()=>{}},navigate:(route,options)=>requests.push({action:'navigate',route,options})};
  window.document.querySelector('openclaw-app').context=context;
  const root={document:window.document,MutationObserver:window.MutationObserver,__mirallyActivity:{memoryBrowser:bridge},requestAnimationFrame:fn=>{const id=++sequence;frames.set(id,fn);return id;},cancelAnimationFrame:id=>frames.delete(id),addEventListener:(name,fn)=>events.set(name,fn)};
  const api=memory.install(root),document=window.document;
  const flush=async()=>{for(let i=0;i<10;i++){await Promise.resolve();const items=[...frames.values()];frames.clear();items.forEach(fn=>fn());}};
  const field=name=>document.querySelector(`[data-memory-filter="${name}"]`);
  const slot=name=>document.querySelector(`[data-memory-slot="${name}"]`);
  const click=name=>document.querySelector(`[data-memory-action="${name}"]`).dispatchEvent(new window.Event('click',{bubbles:true}));
  const submit=()=>slot('filters').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
  const reply=async(result,action)=>{const item=[...requests].reverse().find(row=>row.action===(action || 'search'));assert.ok(item);item.resolve(result);await flush();};
  return {root,api,context,native,document,requests,flush,field,slot,click,submit,reply,cancels:()=>cancels,Event:window.Event};
}
const row=edits=>({id:'summary',title:'Activity synthesis',kind:'episodic',status:'active',provenance:'model_inference',content:'周三讨论设计。',observed_start_utc_ms:1700000000000,observed_end_utc_ms:1700000300000,...edits});

test('memory source labels distinguish rules, model output, confirmation and unknown; fixed titles localize only known templates',()=>{
  assert.equal(memory.sourceLabel(row()),'模型活动总结');assert.equal(memory.sourceLabel(row({kind:'semantic'})),'模型推测');
  assert.equal(memory.sourceLabel(row({provenance:'observed'})),'规则观测记录');assert.equal(memory.sourceLabel(row({provenance:'user_confirmed'})),'用户确认');
  assert.equal(memory.sourceLabel(row({provenance:null})),'来源未标注');
  assert.equal(memory.title(row()),'活动记忆总结');assert.equal(memory.title(row({title:'Observed desktop context'})),'桌面活动观测');
  assert.equal(memory.title(row({title:'Actual private title'})),'Actual private title');
});

test('source, kinds, candidates and local date filters become server-side bounded search parameters',()=>{
  const filters=memory.filters({query:'  工作 ',source:'summaries',kind:'episodic',candidates:true,start:'2026-10-15',end:'2026-10-15'});
  assert.deepEqual(filters.provenances,['model_inference','user_confirmed']);assert.deepEqual(filters.kinds,['episodic']);
  assert.equal(filters.query,'工作');assert.equal(filters.include_candidates,true);assert.equal(filters.limit,20);
  assert.equal(filters.start_utc_ms,new Date(2026,9,15).getTime());assert.equal(filters.end_utc_ms,new Date(2026,9,16).getTime()-1);
  assert.equal(Object.hasOwn(memory.filters({source:'all'}),'provenances'),false);
  for(const fields of [{source:'unknown'},{source:'all',kind:'task'},{source:'all',start:'2026-02-30'},{source:'all',start:'2026-10-16',end:'2026-10-15'}])assert.throws(()=>memory.filters(fields));
});

test('native Memory overview, memories and Agents memory all preserve native content and show owner activity memory',async()=>{
  for(const tag of ['openclaw-memory-settings','openclaw-memory-memories','openclaw-agent-memory-panel']){
    const f=fixture(tag);try{await f.flush();assert.ok(f.document.querySelector('#mirally-memory-browser'));
      const card=f.document.querySelector('#mirally-memory-browser');
      if(tag==='openclaw-memory-settings'){
        assert.equal(card.parentNode.className,'memory-page__panel');
        assert.equal(card.parentNode.previousElementSibling.id,'native-heading');
        assert.equal(card.nextElementSibling.id,'native');
      }else assert.equal(f.native.previousSibling.id,'mirally-memory-browser');
      assert.equal(f.document.querySelector('#native').textContent,'Native memory stays');
      assert.equal(Object.hasOwn(f.requests[0].args,'provenances'),false);assert.equal(f.field('source').value,'all');assert.equal(f.requests[0].action,'search');
    }finally{f.api.dispose();await f.flush();}
  }
});

test('other agents and hidden native panels never expose owner activity memory',async()=>{
  const f=fixture('openclaw-memory-settings','other');try{
    await f.flush();assert.equal(f.requests.length,0);assert.equal(f.document.querySelector('#mirally-memory-browser'),null);
    f.native.selectedAgentId='main';f.native.hidden=true;f.api.sync();await f.flush();assert.equal(f.requests.length,0);
    f.native.hidden=false;f.api.sync();await f.flush();assert.equal(f.requests.length,1);
    f.native.selectedAgentId='other';f.api.sync();await f.flush();assert.equal(f.document.querySelector('#mirally-memory-browser'),null);
  }finally{f.api.dispose();await f.flush();}
});

test('all sources default shows earlier observed history and summaries remain an explicit filter',async()=>{
  const f=fixture();try{
    await f.flush();assert.equal(f.field('source').value,'all');
    assert.equal(Object.hasOwn(f.requests[0].args,'provenances'),false);
    const earlier=row({id:'earlier-rule',provenance:'observed',title:'Observed desktop context',content:'凌晨四点的历史记录',observed_start_utc_ms:Date.parse('2026-10-15T04:00:00+08:00')});
    await f.reply({memories:[earlier]});
    assert.match(f.slot('list').textContent,/桌面活动观测.*规则观测记录.*凌晨四点的历史记录/);
    assert.match(f.document.querySelector('#mirally-memory-browser header').textContent,/默认显示全部已保存记忆，来源分别标注/);
    f.field('source').value='summaries';f.submit();
    assert.deepEqual(f.requests.at(-1).args.provenances,['model_inference','user_confirmed']);
    await f.reply({memories:[row({content:'中午模型整理的记录'})]});
    assert.doesNotMatch(f.slot('list').textContent,/凌晨四点/);assert.match(f.slot('list').textContent,/中午模型整理/);
  }finally{f.api.dispose();await f.flush();}
});

test('native content rerenders restore the contribution below its heading without losing filters or requerying',async()=>{
  const f=fixture();try{
    await f.flush();await f.reply({memories:[row()]});f.field('query').value='未提交的筛选';
    const card=f.document.querySelector('#mirally-memory-browser'),count=f.requests.length;
    const content=f.native.querySelector('.memory-page__panel');
    content.replaceChildren(f.document.createElement('openclaw-memory-memories'));
    f.api.sync();await f.flush();
    assert.equal(content.firstChild,card);assert.equal(card.nextElementSibling.localName,'openclaw-memory-memories');
    assert.equal(content.previousElementSibling.id,'native-heading');
    assert.equal(f.field('query').value,'未提交的筛选');assert.equal(f.requests.length,count);
  }finally{f.api.dispose();await f.flush();}
});

test('empty summaries explain model progress and offer an explicit rule-only search',async()=>{
  const f=fixture();try{
    await f.flush();await f.reply({status:'ready',memories:[]});
    assert.match(f.slot('notice').textContent,/未找到符合条件.*调整关键词、来源或日期/);
    f.field('source').value='summaries';f.submit();
    assert.deepEqual(f.requests.at(-1).args.provenances,['model_inference','user_confirmed']);
    await f.reply({status:'ready',memories:[]});
    assert.match(f.slot('notice').textContent,/当前筛选.*不表示没有历史记录.*所有来源或规则观测记录/);
    assert.equal(f.document.querySelector('[data-memory-action="observed"]').hidden,false);
    f.click('observed');assert.deepEqual(f.requests.at(-1).args.provenances,['observed']);
    await f.reply({memories:[row({provenance:'observed',title:'Observed desktop context'})]});
    assert.match(f.slot('list').textContent,/桌面活动观测.*规则观测记录/);
  }finally{f.api.dispose();await f.flush();}
});

test('source/type/date search pagination keeps the submitted scope rather than later input drafts',async()=>{
  const f=fixture();try{
    await f.flush();await f.reply({memories:[]});
    f.field('query').value='计划';f.field('kind').value='semantic';f.field('source').value='all';f.field('candidates').checked=true;
    f.submit();await f.reply({memories:[row({id:'first'})],next_offset:20,truncated:true});
    f.field('query').value='未提交';f.field('source').value='observed';f.click('more');
    assert.equal(f.requests.at(-1).args.query,'计划');assert.equal(f.requests.at(-1).args.offset,20);
    assert.deepEqual(f.requests.at(-1).args.kinds,['semantic']);assert.equal(Object.hasOwn(f.requests.at(-1).args,'provenances'),false);
    await f.reply({memories:[row({id:'next'})]});assert.equal(f.document.querySelector('[data-memory-action="more"]').hidden,true);
  }finally{f.api.dispose();await f.flush();}
});

test('bounded keyword selection never implies a next page without an offset',async()=>{
  const f=fixture();try{
    await f.flush();await f.reply({memories:[row()],truncated:true,candidate_selection_bounded:true,next_offset:null});
    assert.match(f.slot('notice').textContent,/关键词检索仅展示有限相关结果.*缩小日期或关键词/);
    assert.doesNotMatch(f.slot('notice').textContent,/下一页/);
    assert.equal(f.document.querySelector('[data-memory-action="more"]').hidden,true);
    f.click('refresh');await f.reply({memories:[row()],truncated:true,next_offset:20});
    assert.match(f.slot('notice').textContent,/可继续查看下一页/);
  }finally{f.api.dispose();await f.flush();}
});

test('list/detail text remains inert and paginated detail shows time, provenance, retention and clickable parent trace',async()=>{
  const f=fixture();try{
    await f.flush();await f.reply({memories:[row({title:'<img src=x>',content:'<script>unsafe()</script>'})]});
    assert.equal(f.document.querySelectorAll('.mirally-memory img,.mirally-memory script').length,0);
    f.document.querySelector('.mirally-memory__result').dispatchEvent(new f.Event('click'));
    await f.reply({memory:row({metadata:{source_retention_status:'partially_archived',derived_from_memory_id:'observed-parent'},source_observation_ids:['source-1']}),text:'<img src=x>第一段',from_line:1,line_count:100,truncated:true},'read');
    assert.match(f.slot('meta').textContent,/模型活动总结.*已保存/);assert.match(f.slot('explanation').textContent,/不等于用户确认/);
    assert.match(f.slot('time').textContent,/本地时间/);assert.match(f.slot('source').textContent,/部分原始证据已清理.*source-1/);
    assert.equal(f.document.querySelector('[data-memory-action="detail-more"]').disabled,false);
    f.click('detail-more');assert.equal(f.requests.at(-1).args.from_line,101);
    await f.reply({memory:row({metadata:{derived_from_memory_id:'observed-parent'}}),text:'最后一段',from_line:101,line_count:2,truncated:false},'read');
    assert.equal(f.document.querySelector('[data-memory-action="detail-more"]').hidden,true);
    f.slot('trace').querySelector('button').dispatchEvent(new f.Event('click'));
    assert.equal(f.requests.at(-1).args.memory_id,'observed-parent');
    await f.reply({memory:row({id:'observed-parent',provenance:'observed'}),text:'Desktop evidence observed ...'},'read');
    assert.match(f.slot('explanation').textContent,/规则整理.*不是模型总结/);
    assert.equal(f.document.querySelectorAll('.mirally-memory img,.mirally-memory script').length,0);
  }finally{f.api.dispose();await f.flush();}
});

test('fresh searches invalidate old detail replies and context changes retire all previous results',async()=>{
  const f=fixture();try{
    await f.flush();await f.reply({memories:[row()]});f.document.querySelector('.mirally-memory__result').dispatchEvent(new f.Event('click'));
    const oldRead=f.requests.at(-1);f.submit();await f.reply({memories:[]});oldRead.resolve({memory:row(),text:'late detail'});await f.flush();
    assert.equal(f.slot('detail').hidden,true);
    f.click('refresh');const oldSearch=f.requests.at(-1);
    f.document.querySelector('openclaw-app').context={...f.context,gateway:{snapshot:{client:{}}}};f.api.sync();await f.flush();
    oldSearch.resolve({memories:[row({title:'OLD'})]});await f.flush();assert.doesNotMatch(f.slot('list').textContent,/OLD/);
    await f.reply({memories:[row({title:'NEW'})]});assert.match(f.slot('list').textContent,/NEW/);assert.ok(f.cancels()>0);
    f.native.remove();f.api.sync();await f.flush();assert.equal(f.document.querySelector('#mirally-memory-browser'),null);
  }finally{f.api.dispose();await f.flush();}
});

test('refresh errors remain visible and native replacement remounts exactly one contribution',async()=>{
  const f=fixture();try{await f.flush();f.requests.at(-1).reject(Error('offline'));await f.flush();assert.match(f.slot('notice').textContent,/读取失败.*offline/);
    const replacement=f.document.createElement('openclaw-memory-settings');replacement.selectedAgentId='main';replacement.innerHTML='<header>Native heading</header><div class="memory-page__panel"><p>Native content</p></div>';f.native.replaceWith(replacement);f.api.sync();await f.flush();
    assert.equal(f.document.querySelectorAll('#mirally-memory-browser').length,1);await f.reply({memories:[]});
    f.click('refresh');assert.equal(f.requests.at(-1).action,'search');
  }finally{f.api.dispose();await f.flush();}
});
