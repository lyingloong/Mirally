const {test}=require('node:test');
const assert=require('node:assert/strict');
const {parseHTML}=require('../integrations/openclaw/node_modules/linkedom');
const subagents=require('../mirally/assets/control-ui-subagents.js');
function fixture(){
  const {document}=parseHTML('<html><body><openclaw-chat-page><openclaw-agent-select id="chat"><wa-dropdown-item data-agent-option value="main">Main</wa-dropdown-item><wa-dropdown-item data-agent-option value="worker-custom">Worker</wa-dropdown-item></openclaw-agent-select></openclaw-chat-page><div class="sidebar-agent-menu"><wa-dropdown-item class="sidebar-agent-menu__agent-switch" value="agent:worker-custom">Worker</wa-dropdown-item><wa-dropdown-item class="sidebar-agent-menu__agent-switch" value="agent:main">Main</wa-dropdown-item></div><div class="settings-sidebar"><openclaw-agent-select><wa-dropdown-item data-agent-option value="worker-custom">Settings</wa-dropdown-item></openclaw-agent-select></div><div class="agent-scope-control"><openclaw-agent-select><wa-dropdown-item data-agent-option value="worker-custom">History</wa-dropdown-item></openclaw-agent-select></div><div class="chat-message">worker-custom says hello</div></body></html>');
  const location={pathname:'/chat'};const filter=subagents.createFilter(document,['worker-custom'],()=>location);
  return {document,location,filter,worker:document.querySelector('#chat [value="worker-custom"]'),main:document.querySelector('#chat [value="main"]')};
}
test('custom internal IDs are hidden only from direct chat selection and sidebar switching',()=>{
  const f=fixture();f.filter.sync();
  assert.equal(f.worker.hidden,true);assert.equal(f.worker.disabled,true);
  assert.equal(f.worker.getAttribute('aria-hidden'),'true');
  assert.equal(f.main.hasAttribute('hidden'),false);
  assert.equal(f.document.querySelector('.sidebar-agent-menu__agent-switch[value="agent:worker-custom"]').hidden,true);
  for(const scope of ['.settings-sidebar','.agent-scope-control'])assert.equal(f.document.querySelector(`${scope} wa-dropdown-item`).hasAttribute('hidden'),false);
  assert.equal(f.document.querySelector('.chat-message').textContent,'worker-custom says hello');
});
test('exact IDs and native encoded sidebar values are used without prefix matching',()=>{
  const f=fixture(),menu=f.document.querySelector('.sidebar-agent-menu__agent-switch');
  menu.value='agent:'+encodeURIComponent('internal/custom worker');
  f.worker.value='worker-custom-extra';f.filter.update(['internal/custom worker','worker-custom']);
  assert.equal(menu.hidden,true);assert.equal(f.worker.hasAttribute('hidden'),false);
  menu.value='agent:%broken';f.filter.sync();assert.equal(menu.hasAttribute('hidden'),false);
});
test('Lit reused options restore original properties and respect the replacement option disabled state',()=>{
  const f=fixture();f.worker.setAttribute('aria-hidden','false');f.filter.sync();
  f.worker.value='main';f.document.querySelector('#chat').options=[{value:'main',disabled:false}];f.filter.sync();
  assert.equal(f.worker.hidden,false);assert.equal(f.worker.disabled,false);assert.equal(f.worker.getAttribute('aria-hidden'),'false');
  f.worker.value='worker-custom';f.filter.sync();f.worker.value='main';f.document.querySelector('#chat').options=[{value:'main',disabled:true}];f.filter.sync();
  assert.equal(f.worker.disabled,true);assert.equal(f.worker.hasAttribute('hidden'),false);
});
test('route changes restore generic pickers and preserve their original hidden and disabled attributes',()=>{
  const f=fixture();f.worker.closest('openclaw-chat-page').replaceWith(f.worker.closest('openclaw-agent-select'));
  f.worker.setAttribute('disabled','');f.worker.setAttribute('hidden','');f.filter.sync();
  f.location.pathname='/settings/model-providers';f.filter.sync();
  assert.equal(f.worker.hasAttribute('data-mirally-subagent-hidden'),false);
  assert.equal(f.worker.hasAttribute('hidden'),true);assert.equal(f.worker.hasAttribute('disabled'),true);
  assert.equal(f.filter.blocked(f.worker),false);
});
test('removing internal IDs restores options; empty default does not hide UFO by name',()=>{
  const f=fixture();f.filter.sync();f.filter.update([]);
  assert.equal(f.worker.hidden,false);assert.equal(f.worker.disabled,false);
  f.worker.value='ufo';f.filter.sync();assert.equal(f.worker.hidden,false);
  f.filter.close();
});

test('installation is idempotent, blocks only filtered native selection, and disposes cleanly',()=>{
  const f=fixture(),callbacks=[],events=new Map();let observer;
  const root={document:f.document,location:f.location,
    MutationObserver:class {constructor(callback){this.callback=callback;observer=this;}observe(){}disconnect(){this.disconnected=true;}},
    requestAnimationFrame:callback=>{callbacks.push(callback);return callbacks.length;},cancelAnimationFrame:()=>{},
    addEventListener:(name,callback)=>events.set(name,callback)};
  const api=subagents.install(root,['worker-custom']);
  assert.equal(subagents.install(root,['worker-custom']),api);
  const event=new f.document.defaultView.Event('wa-select',{bubbles:true,cancelable:true});event.detail={item:f.worker};
  f.worker.dispatchEvent(event);assert.equal(event.defaultPrevented,true);
  const mainEvent=new f.document.defaultView.Event('wa-select',{bubbles:true,cancelable:true});mainEvent.detail={item:f.main};
  f.main.dispatchEvent(mainEvent);assert.equal(mainEvent.defaultPrevented,false);
  observer.callback();callbacks.shift()();
  api.dispose();assert.equal(observer.disconnected,true);assert.equal(f.worker.hidden,false);
});

test('default chat page respects a native base path without filtering settings or sessions',()=>{
  const f=fixture();f.worker.closest('openclaw-chat-page').replaceWith(f.worker.closest('openclaw-agent-select'));
  const picker=f.worker.closest('openclaw-agent-select');
  assert.equal(subagents.isChatPicker(picker,{pathname:'/console/',basePath:'/console'}),true);
  assert.equal(subagents.isChatPicker(picker,{pathname:'/console/settings/model-providers',basePath:'/console'}),false);
  assert.equal(subagents.isChatPicker(picker,{pathname:'/console/sessions',basePath:'/console'}),false);
});
