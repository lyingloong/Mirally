const {test}=require('node:test');
const assert=require('node:assert/strict');
const {parseHTML}=require('../integrations/openclaw/node_modules/linkedom');
const files=require('../mirally/assets/control-ui-files.js');

function fixture(html){
  const {window}=parseHTML(`<html><body>${html}</body></html>`),frames=new Map();let sequence=0;
  const root={document:window.document,MutationObserver:window.MutationObserver,
    requestAnimationFrame:fn=>{const id=++sequence;frames.set(id,fn);return id;},cancelAnimationFrame:id=>frames.delete(id),addEventListener(){}};
  const api=files.install(root);
  const flush=async()=>{for(let i=0;i<8;i++){await Promise.resolve();const calls=[...frames.values()];frames.clear();calls.forEach(fn=>fn());}};
  return {document:window.document,Event:window.Event,api,flush,root};
}
const pane=content=>`<openclaw-chat-pane><div class="chat-group assistant"><div class="chat-text">${content}</div></div></openclaw-chat-pane>`;

test('Windows relative file paths normalize separators while preserving Chinese and spaces',()=>{
  assert.equal(files.relativeFilePath('memory\\notice-综合测评材料提交-润色版.md'),'memory/notice-综合测评材料提交-润色版.md');
  assert.equal(files.relativeFilePath('.\\工作材料\\测评 润色版.docx'),'工作材料/测评 润色版.docx');
  assert.equal(files.relativeFilePath('reports/十月\\收支 总表.xlsx'),'reports/十月/收支 总表.xlsx');
  assert.equal(files.relativeFilePath('My Documents\\报告.md'),'My Documents/报告.md');
  assert.equal(files.relativeFilePath('memory\\通知 稿.md'),'memory/通知 稿.md');
});

test('common inline command prefixes are not mistaken for directories containing spaces',()=>{
  for(const value of ['python scripts\\hello.py','python3.12 scripts\\hello.py','py scripts\\hello.py','node tools\\preview.js','npm run tools\\preview.js','npx tools\\preview.js','pnpm exec tools\\preview.js','yarn tools\\preview.js','bun tools\\preview.js','powershell tools\\preview.ps1','pwsh tools\\preview.ps1','cmd /c tools\\preview.cmd','cd My Documents\\报告.md','pushd My Documents\\报告.md','popd My Documents\\报告.md','PYTHON.EXE scripts\\hello.py'])assert.equal(files.relativeFilePath(value),null,value);
});

test('path detection rejects commands, directories, absolute paths, traversal, devices and URLs',()=>{
  for(const value of ['memory/notice.md','notice.md','D:\\work\\notice.md','\\\\server\\share\\notice.md','..\\notice.md','memory\\..\\notice.md','memory\\folder','memory\\notice.md\ncalc','https://example.invalid/a\\b.md','memory\\CON.md','memory\\notice.md && calc','%USERPROFILE%\\notice.md','~\\notice.md','memory\\a?.md','memory\\\\notice.md'])assert.equal(files.relativeFilePath(value),null,value);
});

test('assistant inline paths become native links with original display and no custom reader',async()=>{
  const f=fixture(pane('<p>已保存 <code>memory\\综合 测评.md</code></p>'));try{
    await f.flush();const link=f.document.querySelector('a[data-file-path]');
    assert.equal(link.getAttribute('data-file-path'),'memory/综合 测评.md');
    assert.equal(link.textContent,'memory\\综合 测评.md');assert.equal(link.querySelector('code').textContent,link.textContent);
    assert.equal(link.getAttribute('role'),'button');assert.equal(link.getAttribute('tabindex'),'0');assert.equal(link.hasAttribute('href'),false);
    assert.equal(link.onclick,null);
    f.api.sync();await f.flush();assert.equal(f.document.querySelectorAll('a[data-file-path]').length,1);
  }finally{f.api.dispose();}
});

test('native links, user text, code blocks, nested tool messages and outside pages remain unchanged',async()=>{
  const f=fixture(pane('<a data-file-path="native.md"><code>memory\\native.md</code></a><a href="https://example.invalid"><code>memory\\web.md</code></a><pre><code>memory\\script.md</code></pre><div class="chat-group tool"><code>memory\\tool.md</code></div>')+
    '<openclaw-chat-pane><div class="chat-group user"><div class="chat-text"><code>memory\\user.md</code></div></div></openclaw-chat-pane><div class="chat-group assistant"><div class="chat-text"><code>memory\\outside.md</code></div></div>');try{
    await f.flush();assert.equal(f.document.querySelectorAll('a[data-file-path]').length,1);
    assert.equal(f.document.querySelector('a[data-file-path]').getAttribute('data-file-path'),'native.md');
    assert.equal(f.document.querySelectorAll('code').length,6);
  }finally{f.api.dispose();}
});

test('native click and keyboard delegation receive the path within its originating session pane',async()=>{
  const f=fixture(pane('<code>memory\\第一会话.md</code>')+pane('<code>memory\\第二会话.md</code>'));try{
    const requests=[];[...f.document.querySelectorAll('openclaw-chat-pane')].forEach((node,index)=>{
      node.sessionKey='session-'+index;
      const native=event=>{const link=event.target.closest('a[data-file-path]');if(link)requests.push({sessionKey:node.sessionKey,path:link.getAttribute('data-file-path')});};
      node.addEventListener('click',native);node.addEventListener('keydown',native);
    });
    await f.flush();const links=f.document.querySelectorAll('a[data-file-path]');
    links[1].querySelector('code').dispatchEvent(new f.Event('click',{bubbles:true}));
    links[0].dispatchEvent(new f.Event('keydown',{bubbles:true}));
    assert.deepEqual(requests,[{sessionKey:'session-1',path:'memory/第二会话.md'},{sessionKey:'session-0',path:'memory/第一会话.md'}]);
  }finally{f.api.dispose();}
});

test('streaming and native rerenders are recognized without duplicate links or HTML injection',async()=>{
  const f=fixture(pane('<code>memory\\unfinished</code>'));try{
    await f.flush();assert.equal(f.document.querySelector('a'),null);
    f.document.querySelector('code').textContent='memory\\完成 空格.md';await f.flush();
    assert.equal(f.document.querySelectorAll('a[data-file-path]').length,1);
    const text=f.document.querySelector('.chat-text');text.innerHTML='<code>memory\\重绘.md</code>';await f.flush();
    assert.equal(f.document.querySelectorAll('a[data-file-path]').length,1);assert.equal(f.document.querySelector('a').textContent,'memory\\重绘.md');
    text.querySelector('code').textContent='memory\\新路径.md';await f.flush();assert.equal(text.querySelector('a').getAttribute('data-file-path'),'memory/新路径.md');
    text.querySelector('code').textContent='memory\\<img src=x>.md';await f.flush();assert.equal(text.querySelector('img'),null);assert.equal(text.querySelector('a'),null);
  }finally{f.api.dispose();}
});
