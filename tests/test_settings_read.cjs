const {test} = require('node:test');
const assert = require('node:assert/strict');
const {Readable, Writable} = require('node:stream');

async function api() {return import('../integrations/openclaw/settings-read.mjs');}
function fixture(overrides={}) {
  const calls=[];
  const source={mcp:{servers:{activity:{args:['--root','portable root']}}},
    plugins:{entries:{'mirally-memory':{config:{root:'portable root'}}}},agents:{defaults:{model:{primary:'provider/model'}}}};
  const native={clear:()=>calls.push('clear'),read:async()=>{calls.push('read');return {
    snapshot:{valid:true,config:source,sourceConfig:{authored:true}},pluginMetadataSnapshot:{manifestRegistry:{official:true}}};},
    schema:(registry,authored)=>{calls.push(['schema',registry,authored]);return {uiHints:{official:true}};},
    redact:(config,hints)=>{calls.push(['redact',hints]);return config;},call:async method=>{calls.push(method);return {running:true,ready:true};},
    ...overrides};
  return {native,calls,source};
}

test('one fresh official validated redacted read returns the three profile branches',async()=>{
  const {createSettingsReader}=await api(), f=fixture(), read=createSettingsReader(f.native);
  assert.deepEqual(await read('profiles'),{servers:f.source.mcp.servers,
    plugin:f.source.plugins.entries['mirally-memory'].config,defaults:f.source.agents.defaults});
  await read('profiles');
  assert.equal(f.calls.filter(call=>call==='clear').length,2);
  assert.equal(f.calls.filter(call=>call==='read').length,2);
  assert.equal(f.calls.filter(call=>Array.isArray(call)&&call[0]==='redact').length,2);
  assert.deepEqual(f.calls[2],['schema',{official:true},{authored:true}]);
});

test('invalid config, missing plugin metadata and malformed branches fail closed',async()=>{
  const {createSettingsReader}=await api();
  for(const result of [{snapshot:{valid:false}}, {snapshot:{valid:true}}]) {
    const f=fixture({read:async()=>result});await assert.rejects(createSettingsReader(f.native)('profiles'));
  }
  const f=fixture();f.source.mcp.servers=[];
  await assert.rejects(createSettingsReader(f.native)('profiles'));
});

test('only fixed status RPC is reachable and unknown operations cannot call native code',async()=>{
  const {createSettingsReader}=await api(),f=fixture(),read=createSettingsReader(f.native);
  assert.deepEqual(await read('memory-status'),{running:true,ready:true});
  assert.deepEqual(f.calls,['clear','read','mirally.memory.status']);
  await assert.rejects(read('config.patch'));assert.deepEqual(f.calls,['clear','read','mirally.memory.status']);
  const invalid=fixture({read:async()=>({snapshot:{valid:false}})});
  await assert.rejects(createSettingsReader(invalid.native)('memory-status'));
  assert.equal(invalid.calls.includes('mirally.memory.status'),false);
});

test('JSONL serves multiple reads with one dependency instance and hides native diagnostics',async()=>{
  const {serve}=await api(), f=fixture({call:async()=>{throw Error('private credential');}});
  let loads=0,output='';
  const sink=new Writable({write(chunk,encoding,done){output+=chunk.toString();done();}});
  await serve(Readable.from(['{"id":1,"action":"profiles"}\n{"id":2,"action":"memory-status"}\n{"id":3,"action":"write"}\n']),sink,
    async()=>{loads++;return f.native;});
  const rows=output.trim().split('\n').map(JSON.parse);
  assert.equal(loads,1);assert.equal(rows[0].ok,true);
  assert.equal(rows[1].ok,false);assert.equal(rows[2].ok,false);
  assert.equal(output.includes('private credential'),false);
});
