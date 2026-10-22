/* Fill the native Markdown file-link gap for Windows relative paths. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object' && module.exports)module.exports=api;
  else if(root.top===root && root.location.origin===__MIRALLY_ORIGIN__ && root.chrome?.webview)api.install(root);
})(typeof window==='object'?window:globalThis,function(){
  'use strict';

  function relativeFilePath(value){
    if(typeof value!=='string' || value.length>4096 || !value.includes('\\'))return null;
    const original=value.trim();
    if(/^(?:python(?:\d+(?:\.\d+)*)?|py|node|npm|npx|pnpm|yarn|bun|powershell|pwsh|cmd|cd|pushd|popd)(?:\.exe)?\s+/i.test(original))return null;
    if(!original || /^[\\/]|^[A-Za-z]:|^[~%]/.test(original) || /[<>:"|?*\u0000-\u001f\u007f]/.test(original))return null;
    const path=original.replaceAll('\\','/').replace(/^\.\//,'');
    const parts=path.split('/');
    if(parts.length<2 || parts.some(part=>!part || part==='.' || part==='..' || /[ .]$/.test(part) || /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(part)))return null;
    return /\.[A-Za-z0-9]{1,12}$/.test(parts.at(-1))?path:null;
  }

  function install(root){
    if(root.__mirallyFiles)return root.__mirallyFiles;
    const document=root.document;
    let frame=null,disposed=false;
    function sync(){
      frame=null;if(disposed)return;
      for(const link of document.querySelectorAll('a[data-mirally-file-link]')){
        const code=link.firstElementChild,path=relativeFilePath(code?.textContent);
        if(!path){if(code)link.replaceWith(code);continue;}
        link.setAttribute('data-file-path',path);link.setAttribute('aria-label','打开文件：'+code.textContent);
      }
      for(const code of document.querySelectorAll('openclaw-chat-pane .chat-group.assistant .chat-text code')){
        if(code.closest('a, pre') || !code.closest('.chat-group')?.classList.contains('assistant'))continue;
        const path=relativeFilePath(code.textContent);if(!path)continue;
        // The native transcript owns clicks/keyboard handling and resolves this
        // path with its current session and agent through sessions.files.get.
        const link=document.createElement('a');
        link.className='markdown-file-link';link.setAttribute('role','button');link.setAttribute('tabindex','0');
        link.setAttribute('data-file-path',path);link.setAttribute('data-file-kind','file');
        link.setAttribute('data-mirally-file-link','');
        link.setAttribute('aria-label','打开文件：'+code.textContent);link.title='打开文件';
        code.replaceWith(link);link.append(code);
      }
    }
    function schedule(){if(!disposed && frame===null)frame=root.requestAnimationFrame(sync);}
    const observer=new root.MutationObserver(schedule);
    observer.observe(document,{childList:true,subtree:true,characterData:true});
    function dispose(){disposed=true;observer.disconnect();if(frame!==null)root.cancelAnimationFrame(frame);frame=null;}
    root.addEventListener('pagehide',dispose);
    root.__mirallyFiles={sync:schedule,dispose};schedule();return root.__mirallyFiles;
  }
  return {relativeFilePath,install};
});
