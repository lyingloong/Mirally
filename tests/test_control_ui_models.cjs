const {test} = require('node:test');
const assert = require('node:assert/strict');
const {parseHTML} = require('../integrations/openclaw/node_modules/linkedom');
const models = require('../mirally/assets/control-ui-models.js');

const copy = value => JSON.parse(JSON.stringify(value));
function config() {
  return {
    models:{mode:'merge',providers:{mirally:{baseUrl:'https://example.test/v1',api:'openai-completions',
      apiKey:'__OPENCLAW_REDACTED__',auth:'api-key',headers:{'x-custom':'__OPENCLAW_REDACTED__'},
      models:[{id:'old',name:'Display name',input:['text','image'],contextWindow:32000,maxTokens:4000,cost:{input:1,output:2}},
        {id:'other',name:'Other',reasoning:true}]},other:{baseUrl:'https://other.test/v1',models:[{id:'else'}]}}},
    agents:{defaults:{model:{primary:'mirally/old',fallbacks:['other/else']},utilityModel:'other/else',models:{'other/else':{alias:'Other'}}},
      entries:{worker:{model:{primary:'other/else'}}}},
    gateway:{port:18789},plugins:{entries:{untouched:{enabled:true}}}
  };
}
function draft(overrides={}) {
  return {provider:'mirally',isNewProvider:false,baseUrl:'https://new.test/compatible-mode/v1',api:'openai-completions',
    apiKey:'',originalId:'old',modelId:'old',makeDefault:false,...overrides};
}
function patch(source=config(), edits={}) {
  const form=draft(edits);
  return models.buildPatch(source,form,models.fingerprint(source,form.provider));
}

test('connection patch does not retransmit redacted credentials, headers, or other config',()=>{
  const source=config(), before=copy(source), result=patch(source);
  assert.ok(result.options);
  assert.deepEqual(Object.keys(result.options.raw),['models']);
  assert.deepEqual(Object.keys(result.options.raw.models.providers),['mirally']);
  const provider=result.options.raw.models.providers.mirally;
  assert.equal(provider.baseUrl,'https://new.test/compatible-mode/v1');
  for(const key of ['apiKey','auth','headers']) assert.equal(Object.hasOwn(provider,key),false,key);
  assert.equal(Object.hasOwn(result.options.raw.models,'mode'),false);
  assert.deepEqual(source,before,'building a patch must not mutate the snapshot');
});

test('editing a model preserves metadata and all other models, authorizing exact array replacement',()=>{
  const source=config(), result=patch(source,{modelId:'qwen/qwen3.8-flash'});
  assert.ok(result.options);
  assert.deepEqual(result.options.raw.models.providers.mirally.models,[
    {...source.models.providers.mirally.models[0],id:'qwen/qwen3.8-flash'},source.models.providers.mirally.models[1]
  ]);
  assert.deepEqual(result.options.replacePaths,['models.providers.mirally.models']);
  assert.equal(result.options.raw.agents.defaults.model.primary,'mirally/qwen/qwen3.8-flash');
  assert.equal(Object.hasOwn(result.options.raw.agents.defaults.model,'fallbacks'),false);
});

test('string default model is replaced as a string when its selected ID changes',()=>{
  const source=config(); source.agents.defaults.model='mirally/old';
  assert.equal(patch(source,{modelId:'namespace/new'}).options.raw.agents.defaults.model,'mirally/namespace/new');
});

test('renaming a default model preserves its explicit account suffix',()=>{
  const source=config();source.agents.defaults.model.primary='mirally/old@work';
  assert.equal(patch(source,{modelId:'namespace/new'}).options.raw.agents.defaults.model.primary,'mirally/namespace/new@work');
  source.agents.defaults.model='mirally/old@work';
  assert.equal(patch(source,{modelId:'namespace/new'}).options.raw.agents.defaults.model,'mirally/namespace/new@work');
  assert.equal(patch(source,{modelId:'namespace/new',makeDefault:true}).options.raw.agents.defaults.model,'mirally/namespace/new');
});

test('a model still referenced with an account suffix cannot be renamed',()=>{
  const source=config();source.agents.entries.worker.model.primary='mirally/old@work';
  assert.ok(patch(source,{modelId:'new'}).error);
  assert.ok(patch(source).options);
});

test('non-default model rename leaves default untouched, explicit default selection updates it',()=>{
  const source=config();source.agents.defaults.model.primary='other/else';
  assert.equal(Object.hasOwn(patch(source,{modelId:'new'}).options.raw,'agents'),false);
  assert.equal(patch(source,{modelId:'new',makeDefault:true}).options.raw.agents.defaults.model.primary,'mirally/new');
});

test('new model is appended without dropping metadata or disturbing primary',()=>{
  const source=config(), result=patch(source,{originalId:null,modelId:'namespace/new'});
  assert.ok(result.options);
  assert.deepEqual(result.options.raw.models.providers.mirally.models.slice(0,2),source.models.providers.mirally.models);
  assert.deepEqual(result.options.raw.models.providers.mirally.models[2],{id:'namespace/new',name:'namespace/new',input:['text']});
  assert.equal(Object.hasOwn(result.options.raw,'agents'),false);
});

test('reasoning capability and request dialect are independent from task thinking and preserve unrelated compat',()=>{
  const source=config();source.models.providers.mirally.models[0].compat={supportsDeveloperRole:false,thinkingFormat:'openai'};
  const row=patch(source,{supportsReasoning:true,reasoningDirty:true,thinkingFormat:'qwen-chat-template',thinkingFormatDirty:true,maxTokens:'8192',maxTokensDirty:true}).options.raw.models.providers.mirally.models[0];
  assert.equal(row.reasoning,true);assert.equal(row.maxTokens,8192);
  assert.deepEqual(row.compat,{supportsDeveloperRole:false,thinkingFormat:'qwen-chat-template'});
  assert.equal(Object.hasOwn(patch(source,{supportsReasoning:true,reasoningDirty:true}).options.raw,'agents'),false,'capability is not a task thinking switch');
  const cleared=patch(source,{thinkingFormat:'',thinkingFormatDirty:true}).options.raw.models.providers.mirally.models[0];
  assert.deepEqual(cleared.compat,{supportsDeveloperRole:false});
  assert.deepEqual(source.models.providers.mirally.models[0].compat,{supportsDeveloperRole:false,thinkingFormat:'openai'});
});

test('task defaults patch only explicit thinking and utility edits without touching subagents or fallbacks',()=>{
  const source=config();source.agents.defaults.subagents={model:{primary:'other/else',fallbacks:['mirally/other']},thinking:'low'};
  const result=patch(source,{thinkingDefault:'off',thinkingDirty:true,utilityModel:'',utilityDirty:true}).options.raw;
  assert.deepEqual(result.agents,{defaults:{thinkingDefault:'off',utilityModel:''}});
  assert.equal(Object.hasOwn(patch(source,{thinkingDefault:'high',utilityModel:''}).options.raw,'agents'),false);
  const expected=models.fingerprint(source,'mirally');source.agents.defaults.utilityModel='mirally/other';
  assert.match(models.buildPatch(source,draft({utilityDirty:true,utilityModel:''}),expected).error,/其他操作/);
});

test('thinking and output controls reject invalid authored values before patching',()=>{
  for(const maxTokens of ['',0,-1,1.5,'NaN',Number.MAX_SAFE_INTEGER+1]) assert.ok(patch(config(),{maxTokens,maxTokensDirty:true}).error,String(maxTokens));
  for(const thinkingFormat of ['off','enable_thinking'])assert.ok(patch(config(),{thinkingFormat,thinkingFormatDirty:true}).error);
  assert.ok(patch(config(),{thinkingDefault:'false',thinkingDirty:true}).error);
  assert.ok(patch(config(),{utilityModel:'not-a-reference',utilityDirty:true}).error);
  for(const thinkingFormat of ['openai','openrouter','deepseek','together','qwen','qwen-chat-template','zai'])assert.ok(patch(config(),{thinkingFormat,thinkingFormatDirty:true}).options);
});

test('renames reject duplicate and missing original IDs',()=>{
  assert.ok(patch(config(),{modelId:'other'}).error);
  assert.ok(patch(config(),{originalId:'missing',modelId:'new'}).error);
  assert.ok(patch(config(),{originalId:null,modelId:'old'}).error);
});

test('explicit image capability edits enable and disable existing and new models while preserving metadata',()=>{
  for(const originalId of ['old','other',null]) for(const supportsImages of [true,false]) {
    const source=config(), before=copy(source);
    const result=patch(source,{originalId,modelId:originalId??'new-model',supportsImages,imageDirty:true});
    assert.ok(result.options);
    const rows=result.options.raw.models.providers.mirally.models;
    const selected=rows.find(row=>row.id===(originalId??'new-model'));
    assert.deepEqual(selected.input,supportsImages?['text','image']:['text']);
    for(const row of source.models.providers.mirally.models) if(row.id!==originalId) assert.deepEqual(rows.find(item=>item.id===row.id),row);
    if(originalId==='old') assert.equal(selected.contextWindow,32000);
    assert.deepEqual(source,before);
    assert.equal(Object.hasOwn(result.options.raw.models.providers.mirally,'apiKey'),false);
  }
});

test('unmodified image toggle preserves omitted or existing input declarations',()=>{
  for(const originalId of ['old','other']) {
    const source=config(), result=patch(source,{originalId,modelId:originalId,supportsImages:false,imageDirty:false});
    assert.deepEqual(result.options.raw.models.providers.mirally.models,source.models.providers.mirally.models);
  }
  const source=config();source.models.providers.mirally.models[0].input=['image'];
  assert.deepEqual(patch(source,{supportsImages:true,imageDirty:false}).options.raw.models.providers.mirally.models[0].input,['image']);
});

test('image capability edits preserve authored audio and video modalities',()=>{
  const source=config();source.models.providers.mirally.models[0].input=['text','audio','image','video'];
  assert.deepEqual(patch(source,{supportsImages:false,imageDirty:true}).options.raw.models.providers.mirally.models[0].input,['text','audio','video']);
  assert.deepEqual(patch(source,{supportsImages:true,imageDirty:true}).options.raw.models.providers.mirally.models[0].input,['text','audio','image','video']);
  source.models.providers.mirally.models[0].input=['text','audio','video'];
  assert.deepEqual(patch(source,{supportsImages:true,imageDirty:true}).options.raw.models.providers.mirally.models[0].input,['text','audio','video','image']);
});

test('capability edit still fences concurrent capability changes and preserves redacted model secrets',()=>{
  const source=config();source.models.providers.mirally.models[0].headers={Authorization:'__OPENCLAW_REDACTED__'};
  const edits=draft({supportsImages:false,imageDirty:true}), expected=models.fingerprint(source,'mirally');
  const result=models.buildPatch(source,edits,expected);
  assert.deepEqual(result.options.raw.models.providers.mirally.models[0].headers,source.models.providers.mirally.models[0].headers);
  const concurrent=copy(source);concurrent.models.providers.mirally.models[0].input=['text'];
  assert.ok(models.buildPatch(concurrent,edits,expected).error);
});

test('rename rejects defaults aliases, utility, fallback and per-agent references',()=>{
  for(const modify of [
    c=>c.agents.defaults.utilityModel='mirally/old',
    c=>c.agents.defaults.model.fallbacks.push('mirally/old'),
    c=>c.agents.defaults.models['mirally/old']={alias:'Old'},
    c=>c.agents.entries.worker.model.primary='mirally/old'
  ]) {
    const source=config();modify(source);
    assert.ok(patch(source,{modelId:'new'}).error,'a referenced old ID must not silently become unreachable');
    assert.ok(patch(source,{baseUrl:'https://changed.test/v1'}).options,'unchanged ID is safe');
  }
});

test('rename rejects channel, tool and hook routes still using the original model',()=>{
  for(const modify of [
    c=>c.channels={modelByChannel:{telegram:{chat:'mirally/old'}}},
    c=>c.tools={image:{model:'mirally/old'}},
    c=>c.hooks={gmail:{model:'mirally/old'}},
    c=>c.hooks={mappings:[{model:'mirally/old@work'}]}
  ]) {
    const source=config();modify(source);
    assert.ok(patch(source,{modelId:'new'}).error);
    assert.ok(patch(source).options);
  }
});

test('model-level redacted metadata survives unchanged IDs and blocks unsafe renaming',()=>{
  const source=config();source.models.providers.mirally.models[0].headers={Authorization:'__OPENCLAW_REDACTED__'};
  const unchanged=patch(source);
  assert.deepEqual(unchanged.options.raw.models.providers.mirally.models[0].headers,{Authorization:'__OPENCLAW_REDACTED__'});
  assert.ok(patch(source,{modelId:'new'}).error,'official redaction restoration cannot match renamed model to old ID');
  assert.ok(patch(source,{originalId:'other',modelId:'other-renamed'}).options,'an unrelated redacted entry is safe by stable ID');
});

test('fingerprint fences same-provider and default changes without blocking unrelated providers',()=>{
  const source=config(), expected=models.fingerprint(source,'mirally'), edits=draft();
  for(const modify of [c=>c.models.providers.mirally.baseUrl='https://concurrent.test',c=>c.models.providers.mirally.models[0].name='Concurrent',c=>c.agents.defaults.model.primary='other/else']) {
    const latest=copy(source);modify(latest);
    assert.ok(models.buildPatch(latest,edits,expected).error);
  }
  const latest=copy(source);latest.models.providers.other.baseUrl='https://unrelated.test';
  assert.ok(models.buildPatch(latest,edits,expected).options);
});

test('new inline key selects explicit API-key authentication, blank key preserves original source',()=>{
  const source=config();delete source.models.providers.mirally.auth;
  const keyed=patch(source,{apiKey:'  replacement-test-key  '}).options.raw.models.providers.mirally;
  assert.equal(keyed.apiKey,'replacement-test-key');assert.equal(keyed.auth,'api-key');
  const blank=patch(source,{apiKey:'   '}).options.raw.models.providers.mirally;
  assert.equal(Object.hasOwn(blank,'apiKey'),false);assert.equal(Object.hasOwn(blank,'auth'),false);
});

test('provider and model validation rejects ambiguous or unsafe IDs',()=>{
  for(const provider of ['', '__proto__','constructor','prototype','bad.provider','bad/provider','white space'])
    assert.ok(models.validate(draft({provider})),provider);
  for(const modelId of ['', ' ', 'two words','/model','model/','namespace//model'])
    assert.ok(models.validate(draft({modelId})),JSON.stringify(modelId));
  assert.equal(models.validate(draft({modelId:'qwen/qwen3.8-flash'})),null);
  assert.equal(models.validate(draft({provider:'local-provider_2'})),null);
});

test('URL validation allows portable HTTP endpoints and rejects embedded credentials or tokens',()=>{
  for(const baseUrl of ['not a URL','file:///tmp/model','https://user:pass@example.test/v1','https://example.test/v1?key=secret','https://example.test/v1#token'])
    assert.ok(models.validate(draft({baseUrl})),baseUrl);
  for(const baseUrl of ['http://localhost:1234/v1','https://example.test/workspaces/demo/compatible-mode/v1'])
    assert.equal(models.validate(draft({baseUrl})),null);
  assert.ok(models.validate(draft({api:'unsupported'})));
});

test('new providers require key and cannot overwrite an existing provider',()=>{
  const source=config(), form=draft({provider:'new-provider',isNewProvider:true,originalId:null,modelId:'namespace/new'});
  assert.ok(models.buildPatch(source,form,models.fingerprint(source,form.provider)).error);
  form.apiKey='new-test-key';form.makeDefault=true;
  const result=models.buildPatch(source,form,models.fingerprint(source,form.provider));
  assert.ok(result.options);
  assert.equal(result.options.raw.agents.defaults.model.primary,'new-provider/namespace/new');
  assert.equal(result.options.raw.models.providers['new-provider'].auth,'api-key');
  assert.ok(patch(source,{isNewProvider:true,apiKey:'test-key'}).error);
  assert.ok(patch(source,{provider:'removed'}).error);
});

test('Responses operation URLs are rejected with the correct portable API base',()=>{
  for(const baseUrl of ['https://relay.test/api/gpt/v1/responses','https://relay.test/api/gpt/v1/responses/']) {
    const form=draft({api:'openai-responses',baseUrl});
    assert.match(models.validate(form),/https:\/\/relay\.test\/api\/gpt\/v1/);
    assert.ok(patch(config(),form).error,'invalid endpoint must never reach config.patch');
  }
  assert.equal(models.validate(draft({api:'openai-responses',baseUrl:'https://relay.test/api/gpt/v1'})),null);
});

test('Chat Completions URLs are rejected without dropping custom prefix or port',()=>{
  assert.match(models.validate(draft({baseUrl:'http://localhost:1234/custom/v1/chat/completions/'})),/http:\/\/localhost:1234\/custom\/v1/);
  assert.equal(models.validate(draft({baseUrl:'http://localhost:1234/custom/v1/'})),null);
});

test('Anthropic operation URL advice removes the SDK-owned v1/messages path',()=>{
  assert.match(models.validate(draft({api:'anthropic-messages',baseUrl:'https://relay.test/anthropic/v1/messages/'})),/基础地址 https:\/\/relay\.test\/anthropic；/);
  assert.equal(models.validate(draft({api:'anthropic-messages',baseUrl:'https://relay.test/anthropic'})),null);
});

test('configuration view uses authored snapshot before resolved defaults and never exposes actual keys',()=>{
  const source=config();
  assert.equal(models.configOf({state:{configSnapshot:{sourceConfig:source,resolved:{different:true}}}}),source);
  assert.equal(models.configOf({state:{configSnapshot:{resolved:source}}}),source);
  assert.equal(models.configOf({state:{configSnapshot:{config:source}}}),source);
  assert.equal(models.primary({agents:{defaults:{model:'provider/namespace/model'}}}),'provider/namespace/model');
});

function fixture() {
  const {window}=parseHTML('<html><body><openclaw-app></openclaw-app><openclaw-model-providers-page><div class="settings-page"><div id="official">Official provider controls</div></div></openclaw-model-providers-page></body></html>');
  // linkedom implements a read-only select value; supply browser-equivalent selection for this fixture.
  const proto=Object.getPrototypeOf(window.document.createElement('select'));
  const descriptor=Object.getOwnPropertyDescriptor(proto,'value');
  if(!descriptor?.set) Object.defineProperty(proto,'value',{configurable:true,get(){return this._selectedValue ?? this.querySelector('option')?.value ?? '';},set(value){this._selectedValue=value;}});
  const frames=new Map(),events=new Map(),listeners=new Set(),patches=[];let seq=0, calls=0;
  const source=config(), client={};
  const runtime={state:{connected:true,client,configSnapshot:{sourceConfig:source}},canPatch:true,
    ensureLoaded:async()=>{},refresh:async options=>{options?.beforeApplySnapshot?.();},subscribe:cb=>{listeners.add(cb);return()=>listeners.delete(cb);},
    patchFromSnapshot:async builder=>{calls++;const result=builder(source);if(result.error)return false;patches.push(result.options);return true;}};
  const context={runtimeConfig:runtime,router:{subscribe:()=>()=>{}}};
  window.document.querySelector('openclaw-app').context=context;
  const root={document:window.document,MutationObserver:window.MutationObserver,location:{pathname:'/model-providers'},
    requestAnimationFrame:cb=>{const id=++seq;frames.set(id,cb);return id;},cancelAnimationFrame:id=>frames.delete(id),
    addEventListener:(name,cb)=>events.set(name,cb)};
  const api=models.install(root);
  const flush=async()=>{for(let i=0;i<8;i++){await Promise.resolve();const pending=[...frames.values()];frames.clear();pending.forEach(cb=>cb());}};
  const field=name=>root.document.querySelector(`[data-field="${name}"]`);
  const input=(name,value)=>{field(name).value=value;field(name).dispatchEvent(new window.Event('input',{bubbles:true}));};
  const click=action=>root.document.querySelector(`[data-action="${action}"]`).dispatchEvent(new window.Event('click',{bubbles:true}));
  return {root,api,runtime,context,events,flush,field,input,click,patches,notify:()=>listeners.forEach(cb=>cb()),calls:()=>calls,Event:window.Event};
}

test('image checkbox reads selected model and explicit edits reach the official patch queue',async()=>{
  const f=fixture();try{
    await f.flush();assert.equal(f.field('supportsImages').checked,true);
    f.field('model').value='other';f.field('model').dispatchEvent(new f.Event('change',{bubbles:true}));
    assert.equal(f.field('supportsImages').checked,false);
    f.input('baseUrl','https://connection-only.test/v1');f.click('save');await f.flush();
    assert.equal(Object.hasOwn(f.patches[0].raw.models.providers.mirally.models[1],'input'),false);
    f.field('supportsImages').checked=true;f.field('supportsImages').dispatchEvent(new f.Event('change',{bubbles:true}));
    f.click('save');await f.flush();
    assert.deepEqual(f.patches[1].raw.models.providers.mirally.models[1].input,['text','image']);
    f.field('model').value='old';f.field('model').dispatchEvent(new f.Event('change',{bubbles:true}));
    assert.equal(f.field('supportsImages').checked,true);
    f.field('supportsImages').checked=false;f.field('supportsImages').dispatchEvent(new f.Event('input',{bubbles:true}));
    f.click('save');await f.flush();
    assert.deepEqual(f.patches[2].raw.models.providers.mirally.models[0].input,['text']);
    f.field('model').value='__new__';f.field('model').dispatchEvent(new f.Event('change',{bubbles:true}));
    assert.equal(f.field('supportsImages').checked,false);
    f.input('modelId','new-vision');f.field('supportsImages').checked=true;
    f.field('supportsImages').dispatchEvent(new f.Event('input',{bubbles:true}));
    f.click('save');await f.flush();
    assert.deepEqual(f.patches[3].raw.models.providers.mirally.models[2].input,['text','image']);
  }finally{f.api.dispose();}
});

test('native Models controls remain present; draft and secret clear when leaving the page',async()=>{
  const f=fixture();try{
    await f.flush();assert.ok(f.root.document.querySelector('#official'));assert.ok(f.field('apiKey'));
    assert.equal(f.field('apiKey').value,'');f.input('apiKey','temporary-test-key');
    f.root.location.pathname='/chat';f.api.sync();await f.flush();
    assert.equal(f.root.document.querySelector('#mirally-model-services'),null);
    f.root.location.pathname='/model-providers';f.api.sync();await f.flush();assert.equal(f.field('apiKey').value,'');
  }finally{f.api.dispose();}
});

test('model page explains capability, task thinking and distinct small task routes and submits explicit edits',async()=>{
  const f=fixture();try{
    const source=f.runtime.state.configSnapshot.sourceConfig;
    source.models.providers.mirally.models[0].reasoning=true;
    source.models.providers.mirally.models[0].compat={thinkingFormat:'qwen'};
    source.agents.defaults.thinkingDefault='off';
    await f.flush();
    assert.equal(f.field('supportsReasoning').checked,true);assert.equal(f.field('thinkingFormat').value,'qwen');
    assert.equal(f.field('maxTokens').value,'4000');assert.equal(f.field('thinkingDefault').value,'off');
    const text=f.root.document.querySelector('#mirally-model-services').textContent;
    assert.match(text,/不是任务的思考开关/);assert.match(text,/轻量辅助任务：other\/else/);assert.match(text,/子代理默认/);assert.match(text,/活动记忆整理/);
    f.input('thinkingFormat','qwen-chat-template');f.input('maxTokens','2048');f.input('thinkingDefault','low');f.input('utilityModel','');
    f.click('save');await f.flush();
    const raw=f.patches[0].raw;
    assert.equal(raw.models.providers.mirally.models[0].compat.thinkingFormat,'qwen-chat-template');
    assert.equal(raw.models.providers.mirally.models[0].maxTokens,2048);
    assert.deepEqual(raw.agents.defaults,{thinkingDefault:'low',utilityModel:''});
    f.input('utilityMode','primary');f.click('save');await f.flush();
    assert.deepEqual(f.patches[1].raw.agents.defaults,{utilityModel:''},'explicit primary choice works even when the model ID input was already empty');
  }finally{f.api.dispose();}
});

test('activity memory card uses its separate bridge, preserves other settings, and fences late responses',async()=>{
  const f=fixture();let cancelled=0;const writes=[];
  f.root.__mirallyActivity={modelSettings:{read:async()=>({enabled:false,model:'',effective_model:'mirally/old',max_output_tokens:1500,canSave:true}),
    save:async value=>{writes.push(value);return {saved:true,applied:false,reload_error:'offline'};},cancel:()=>{cancelled++;}}};
  try{
    await f.flush();const memory=name=>f.root.document.querySelector(`[data-memory-field="${name}"]`);
    assert.equal(memory('model').value,'');assert.equal(memory('maxOutputTokens').value,'1500');
    assert.equal(f.root.document.querySelector('[data-action="save-memory"]').disabled,false);
    memory('model').value='other/else';memory('model').dispatchEvent(new f.Event('input',{bubbles:true}));
    memory('maxOutputTokens').value='2048';memory('maxOutputTokens').dispatchEvent(new f.Event('input',{bubbles:true}));f.click('save-memory');await f.flush();
    assert.deepEqual(writes,[{model:'other/else',maxOutputTokens:2048}]);assert.equal(f.calls(),0);
    assert.match(f.root.document.querySelector('[data-slot="memory-notice"]').textContent,/保存.*尚未应用.*offline/);
    memory('maxOutputTokens').value='0';memory('maxOutputTokens').dispatchEvent(new f.Event('input',{bubbles:true}));f.click('save-memory');await f.flush();assert.equal(writes.length,1);
    f.runtime.canPatch=false;f.notify();await f.flush();assert.equal(memory('model').disabled,true);
    f.root.location.pathname='/chat';f.api.sync();await f.flush();assert.ok(cancelled>0);
  }finally{f.api.dispose();}
});

test('saving the primary reloads inherited memory route without erasing an unsaved memory draft',async()=>{
  const f=fixture();const source=f.runtime.state.configSnapshot.sourceConfig;
  f.root.__mirallyActivity={modelSettings:{read:async()=>({enabled:true,model:'',effective_model:models.primary(source),max_output_tokens:1500,canSave:true}),cancel(){}}};
  f.runtime.patchFromSnapshot=async builder=>{const result=builder(source);if(result.error)return false;if(result.options.raw.agents?.defaults?.model)source.agents.defaults.model={...source.agents.defaults.model,...result.options.raw.agents.defaults.model};return true;};
  try{
    await f.flush();const memory=f.root.document.querySelector('[data-memory-field="model"]');
    memory.value='other/unsaved';memory.dispatchEvent(new f.Event('input',{bubbles:true}));
    f.field('model').value='other';f.field('model').dispatchEvent(new f.Event('change',{bubbles:true}));
    f.field('makeDefault').checked=true;f.field('makeDefault').dispatchEvent(new f.Event('change',{bubbles:true}));
    f.click('save');await f.flush();
    assert.match(f.root.document.querySelector('[data-slot="memory-notice"]').textContent,/实际模型：mirally\/other/);
    assert.equal(memory.value,'other/unsaved');
    assert.equal(f.field('thinkingDefault').querySelector('option[value=""]').hasAttribute('disabled'),true);
  }finally{f.api.dispose();}
});

test('late memory bridge reads cannot populate a different client and unavailable settings stay disabled',async()=>{
  const f=fixture();const replies=[];
  f.root.__mirallyActivity={modelSettings:{read:()=>new Promise(resolve=>replies.push(resolve)),cancel(){}}};
  try{
    await f.flush();assert.equal(f.root.document.querySelector('[data-action="save-memory"]').disabled,true);
    f.runtime.state.client={};f.notify();await f.flush();
    replies[0]({model:'old/secret-route',max_output_tokens:1024,canSave:true});await f.flush();
    assert.equal(f.root.document.querySelector('[data-memory-field="model"]').value,'');
    assert.equal(f.root.document.querySelector('[data-action="save-memory"]').disabled,true);
    replies[1]({model:'new/route',max_output_tokens:1024,canSave:false});await f.flush();
    assert.equal(f.root.document.querySelector('[data-memory-field="model"]').value,'new/route');
    assert.equal(f.root.document.querySelector('[data-action="save-memory"]').disabled,true);
  }finally{f.api.dispose();}
});

test('memory model backend read errors stay explicit and cannot enable editing',async()=>{
  const f=fixture();f.root.__mirallyActivity={modelSettings:{read:async()=>({canSave:false,error:'记忆配置读取失败：fixture'}),cancel(){}}};
  try{await f.flush();assert.equal(f.root.document.querySelector('[data-slot="memory-notice"]').textContent,'记忆配置读取失败：fixture');
    assert.equal(f.root.document.querySelector('[data-action="save-memory"]').disabled,true);
  }finally{f.api.dispose();}
});

test('independent memory drafts never overwrite another view model or output change and no-op saves do not write',async()=>{
  const f=fixture(),state={model:'old/model',max_output_tokens:1500,canSave:true},writes=[];
  f.root.__mirallyActivity={modelSettings:{read:async()=>({...state}),save:async edits=>{
    writes.push(edits);if(Object.hasOwn(edits,'model'))state.model=edits.model;if(Object.hasOwn(edits,'maxOutputTokens'))state.max_output_tokens=edits.maxOutputTokens;
    return {saved:true,applied:true};},cancel(){}}};
  try{
    await f.flush();f.click('save-memory');await f.flush();assert.equal(writes.length,0);
    const edit=(name,value)=>{const node=f.root.document.querySelector(`[data-memory-field="${name}"]`);node.value=value;node.dispatchEvent(new f.Event('input',{bubbles:true}));};
    state.model='external/model';edit('maxOutputTokens','2048');f.click('save-memory');await f.flush();
    assert.deepEqual(writes[0],{maxOutputTokens:2048});assert.equal(state.model,'external/model');
    assert.equal(f.root.document.querySelector('[data-memory-field="model"]').value,'external/model');
    state.max_output_tokens=4096;edit('model','');f.click('save-memory');await f.flush();
    assert.deepEqual(writes[1],{model:''});assert.equal(state.max_output_tokens,4096);
    assert.equal(f.root.document.querySelector('[data-memory-field="maxOutputTokens"]').value,'4096');
    f.click('save-memory');await f.flush();assert.equal(writes.length,2);
  }finally{f.api.dispose();}
});

test('permissions prevent saving and dirty changes prevent silent selection loss',async()=>{
  const f=fixture();try{
    await f.flush();f.input('modelId','new');const select=f.field('provider');select.value='other';
    select.dispatchEvent(new f.Event('change',{bubbles:true}));
    assert.equal(select.value,'mirally');assert.equal(f.field('modelId').value,'new');
    f.runtime.canPatch=false;f.notify();await f.flush();
    assert.equal(f.root.document.querySelector('[data-action="save"]').disabled,true);
    f.click('save');await f.flush();assert.equal(f.calls(),0);
  }finally{f.api.dispose();}
});

test('changing Gateway client clears old credentials and reloads the configuration',async()=>{
  const f=fixture();try{
    await f.flush();f.input('apiKey','temporary-test-key');f.runtime.state.client={};f.notify();await f.flush();
    assert.equal(f.field('apiKey').value,'');assert.equal(f.field('modelId').value,'old');
  }finally{f.api.dispose();}
});

test('explicit reload needs an applied snapshot, not a resolved void or stale snapshot',async()=>{
  const f=fixture();try{
    await f.flush();let requests=0;
    f.runtime.refresh=async()=>{requests++;};
    f.click('reload');await f.flush();
    assert.equal(requests,1);
    assert.equal(f.root.document.querySelector('[data-action="save"]').disabled,true);
    assert.match(f.root.document.querySelector('[data-slot="notice"]').textContent,/加载失败/);
    f.runtime.refresh=async options=>{requests++;options.beforeApplySnapshot();};
    f.click('reload');await f.flush();
    assert.equal(requests,2);
    assert.equal(f.root.document.querySelector('[data-action="save"]').disabled,false);
  }finally{f.api.dispose();}
});

test('an acknowledged save with a skipped refresh remains saved but blocks further stale edits',async()=>{
  const f=fixture();try{
    await f.flush();f.runtime.refresh=async()=>{};
    f.input('apiKey','temporary-test-key');f.click('save');await f.flush();
    assert.equal(f.calls(),1);
    assert.equal(f.field('apiKey').value,'');
    assert.match(f.root.document.querySelector('[data-slot="notice"]').textContent,/已保存，但刷新未成功/);
    assert.equal(f.root.document.querySelector('[data-action="save"]').disabled,true);
  }finally{f.api.dispose();}
});
