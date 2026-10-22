const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function fixture(origin='http://127.0.0.1:18789') {
  const messages=[], handlers={};
  const field={value:'', matches:()=>true};
  let focused=true;
  const document={activeElement:field, hasFocus:()=>focused,
    addEventListener:(name,fn)=>handlers[name]=fn};
  const window={chrome:{webview:{postMessage:message=>messages.push(message)}},
    addEventListener:(name,fn)=>handlers[name]=fn};
  vm.runInNewContext(fs.readFileSync('mirally/assets/control-ui-ufo-input.js','utf8')
    .replace('__MIRALLY_ORIGIN__',JSON.stringify('http://127.0.0.1:18789')), {
      window, document, location:{origin}, setInterval:fn=>{handlers.tick=fn;}});
  return {messages,field,handlers,blur:()=>{focused=false;handlers.blur();}};
}
test('only nonempty focused editing revokes desktop access; sending releases it',()=>{
  const f=fixture();
  assert.equal(f.messages.at(-1).editing,false);
  f.field.value='Private user draft';f.handlers.input();
  assert.equal(f.messages.at(-1).editing,true);
  assert.doesNotMatch(JSON.stringify(f.messages),/Private user draft/);
  f.field.value='';f.handlers.tick();
  assert.equal(f.messages.at(-1).editing,false);
});
test('other origins cannot influence execution',()=>{
  assert.equal(fixture('https://example.com').messages.length,0);
});
test('losing focus releases the editing claim',()=>{
  const f=fixture();f.field.value='draft';f.handlers.input();f.blur();
  assert.equal(f.messages.at(-1).editing,false);
});
