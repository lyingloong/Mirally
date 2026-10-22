/* Internal executors remain available to native history and administration. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else if (root.top === root && root.location.origin === __MIRALLY_ORIGIN__ && root.chrome?.webview) api.install(root, __MIRALLY_SUBAGENT_IDS__);
})(typeof window === 'object' ? window : globalThis, function () {
  'use strict';
  const MARKER = 'data-mirally-subagent-hidden';
  const idsOf = ids => new Set((Array.isArray(ids) ? ids : []).filter(id => typeof id === 'string' && id.length));
  function itemId(node) {
    const value = node?.value ?? node?.getAttribute?.('value');
    if (typeof value !== 'string') return null;
    if (!node.classList.contains('sidebar-agent-menu__agent-switch')) return value;
    if (!value.startsWith('agent:')) return null;
    try { return decodeURIComponent(value.slice(6)); } catch { return null; }
  }
  function isChatPicker(picker, location) {
    if (!picker || picker.closest('.agent-scope-control, .settings-sidebar, [class*="settings"], openclaw-sessions-page, openclaw-agents-page')) return false;
    // Specific composer/sidebar hosts are safe even with a base path. Generic
    // pickers are filtered only on the chat route, never in settings/history.
    if (picker.closest('openclaw-chat-page, .chat-composer, .sidebar-agent-menu')) return true;
    const pathname = location?.pathname || '/';
    const base = (location?.basePath || '').replace(/\/$/, '');
    const path = base && (pathname === base || pathname.startsWith(base + '/')) ? pathname.slice(base.length) || '/' : pathname;
    return /\/chat\/?$/.test(path) || path === '/';
  }
  function candidates(document, location) {
    const nodes = new Set(document.querySelectorAll('wa-dropdown-item.sidebar-agent-menu__agent-switch'));
    for (const picker of document.querySelectorAll('openclaw-agent-select')) {
      if (isChatPicker(picker, location)) for (const node of picker.querySelectorAll('wa-dropdown-item[data-agent-option]')) nodes.add(node);
    }
    return nodes;
  }
  function createFilter(document, ids, location = () => ({pathname:'/'})) {
    let internal = idsOf(ids);
    const owned = new Map();
    function restore(node, saved) {
      for (const [name,value] of Object.entries(saved)) {
        if (value === null) { if (node.hasAttribute(name)) node.removeAttribute(name); }
        else if (node.getAttribute(name) !== value) node.setAttribute(name,value);
      }
      if (node.hasAttribute(MARKER)) node.removeAttribute(MARKER);
      // Lit may reuse an option for another agent. Its currently configured
      // disabled state takes precedence over the previous item's saved state.
      const picker=node.closest('openclaw-agent-select');
      const option=picker?.options?.find?.(item => item.value === itemId(node));
      if (picker && (picker.disabled === true || option)) {
        const disabled = picker.disabled === true || option?.disabled === true;
        node.disabled=disabled;
        if(disabled)node.setAttribute('disabled','');else node.removeAttribute('disabled');
      } else node.disabled=node.hasAttribute('disabled');
      node.hidden=node.hasAttribute('hidden');
    }
    function sync() {
      const eligible=candidates(document, location());
      for (const [node,saved] of owned) {
        if(!eligible.has(node) || !internal.has(itemId(node))) {restore(node,saved);owned.delete(node);}
      }
      for (const node of eligible) {
        if (!internal.has(itemId(node))) continue;
        if (!owned.has(node)) owned.set(node,Object.fromEntries(['hidden','disabled','aria-hidden'].map(name=>[name,node.getAttribute(name)])));
        for (const [name,value] of [[MARKER,''],['hidden',''],['disabled',''],['aria-hidden','true']]) if(node.getAttribute(name)!==value)node.setAttribute(name,value);
        if(node.hidden!==true)node.hidden=true;
        if(node.disabled!==true)node.disabled=true;
      }
    }
    function blocked(node) { return candidates(document,location()).has(node) && internal.has(itemId(node)); }
    function close(){for(const [node,saved]of owned)restore(node,saved);owned.clear();}
    function update(ids){internal=idsOf(ids);sync();}
    return {sync,blocked,close,update};
  }
  function install(root,ids=[]) {
    if(root.__mirallySubagents){root.__mirallySubagents.update(ids);return root.__mirallySubagents;}
    const filter=createFilter(root.document,ids,()=>({pathname:root.location.pathname,basePath:root.document.querySelector('openclaw-app')?.context?.basePath || ''}));
    let frame=null,disposed=false;
    function schedule(){if(!disposed && frame===null)frame=root.requestAnimationFrame(()=>{frame=null;filter.sync();});}
    const observer=new root.MutationObserver(schedule);
    observer.observe(root.document,{subtree:true,childList:true,attributes:true,attributeFilter:['value','class','disabled','hidden','aria-hidden']});
    const prepare=()=>filter.sync();
    const prevent=event=>{
      const node=event.detail?.item;
      if(node && filter.blocked(node)){event.preventDefault();event.stopImmediatePropagation();}
    };
    root.document.addEventListener('pointerdown',prepare,true);
    root.document.addEventListener('focusin',prepare,true);
    root.document.addEventListener('wa-select',prevent,true);
    root.addEventListener('popstate',schedule);
    function dispose(){disposed=true;observer.disconnect();if(frame!==null)root.cancelAnimationFrame(frame);root.document.removeEventListener('pointerdown',prepare,true);root.document.removeEventListener('focusin',prepare,true);root.document.removeEventListener('wa-select',prevent,true);filter.close();}
    root.addEventListener('pagehide',dispose);
    const api={sync:filter.sync,update:filter.update,dispose};root.__mirallySubagents=api;filter.sync();return api;
  }
  return {itemId,isChatPicker,createFilter,install};
});
