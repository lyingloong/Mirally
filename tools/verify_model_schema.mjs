/** Validate product model fields against the installed native OpenClaw schema, offline. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import os from 'node:os';
import {spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const nativeRoot = path.join(root, 'integrations/openclaw/node_modules/openclaw');

export async function verifyModelSchema({cli = false} = {}) {
  // Use the installed public config entry rather than a hash-named internal bundle.
  // This validator performs no provider requests or private config reads/writes.
  const {validateConfigObjectRaw, MODEL_THINKING_FORMATS} = await import(pathToFileURL(path.join(nativeRoot, 'dist/config/config.js')).href);
  const baseline = () => ({models:{providers:{fixture:{baseUrl:'https://fixture.invalid/v1',api:'openai-completions',
    models:[{id:'namespace/model',name:'Fixture model',input:['text','image'],reasoning:true,maxTokens:4096}]}}},
    agents:{entries:{main:{}},defaults:{model:{primary:'fixture/namespace/model',fallbacks:[]}}}});
  const findings=[];
  const accept = (label, change) => {
    const config=baseline();change(config);
    const result=validateConfigObjectRaw(config);
    assert.equal(result.ok,true,`${label}: ${JSON.stringify(result.issues)}`);
    findings.push(label);
  };
  const reject = (label, change) => {
    const config=baseline();change(config);
    const result=validateConfigObjectRaw(config);
    assert.equal(result.ok,false,label);
    findings.push(label);
  };
  for(const format of MODEL_THINKING_FORMATS) accept(`native thinking payload dialect ${format}`,config=>{
    config.models.providers.fixture.models[0].compat={thinkingFormat:format};
  });
  for(const level of ['off','minimal','low','medium','high','xhigh','adaptive','max','ultra']) accept(`native default thinking ${level}`,config=>{
    config.agents.defaults.thinkingDefault=level;
  });
  accept('reasoning capability false is distinct from thinking effort off',config=>{
    config.models.providers.fixture.models[0].reasoning=false;config.agents.defaults.thinkingDefault='off';
  });
  for(const utilityModel of ['', 'fixture/namespace/model']) accept(`native utility route ${utilityModel || 'disabled'}`,config=>{
    config.agents.defaults.utilityModel=utilityModel;
  });
  accept('per-model output parameters and subagent route use their native fields',config=>{
    config.agents.defaults.models={'fixture/namespace/model':{params:{thinking:'off',maxTokens:512}}};
    config.agents.defaults.subagents={model:{primary:'fixture/namespace/model'},thinking:'off'};
  });
  reject('reject unknown thinking payload dialect',config=>{config.models.providers.fixture.models[0].compat={thinkingFormat:'invented'};});
  reject('reject non-boolean reasoning capability',config=>{config.models.providers.fixture.models[0].reasoning='on';});
  reject('reject zero model output capacity',config=>{config.models.providers.fixture.models[0].maxTokens=0;});
  reject('reject invalid default thinking level',config=>{config.agents.defaults.thinkingDefault='on';});
  reject('reject non-string utility route',config=>{config.agents.defaults.utilityModel=true;});
  reject('reject invented utility output budget field',config=>{config.agents.defaults.utilityMaxTokens=512;});
  const require=createRequire(import.meta.url);
  const models=require('../mirally/assets/control-ui-models.js');
  const source=baseline();
  source.models.providers.fixture.models[0].compat={thinkingFormat:'openai',supportsStore:false};
  const draft={provider:'fixture',isNewProvider:false,baseUrl:'https://fixture.invalid/v1',api:'openai-completions',apiKey:'',
    originalId:'namespace/model',modelId:'namespace/model',makeDefault:false,
    supportsReasoning:true,reasoningDirty:true,thinkingFormat:'qwen',thinkingFormatDirty:true,
    maxTokens:'8192',maxTokensDirty:true,thinkingDefault:'off',thinkingDirty:true,utilityModel:'fixture/namespace/model',utilityDirty:true};
  const patch=models.buildPatch(source,draft,models.fingerprint(source,'fixture'));
  assert.ok(patch.options,patch.error);
  const merged=structuredClone(source);
  Object.assign(merged.models.providers.fixture,patch.options.raw.models.providers.fixture);
  Object.assign(merged.agents.defaults,patch.options.raw.agents.defaults);
  assert.equal(validateConfigObjectRaw(merged).ok,true,'product-authored capability and routing patch must satisfy native schema');
  assert.equal(merged.models.providers.fixture.models[0].compat.supportsStore,false,'unrelated compatibility metadata survives');
  assert.equal(merged.models.providers.fixture.models[0].compat.thinkingFormat,'qwen');
  assert.equal(merged.agents.defaults.thinkingDefault,'off');
  assert.equal(merged.agents.defaults.utilityModel,'fixture/namespace/model');
  findings.push('product capability, reasoning transport, output and task route patch satisfies native schema');
  if(cli) {
    const temporary=await fs.mkdtemp(path.join(os.tmpdir(),'mirally 模型 schema '));
    try {
      const configPath=path.join(temporary,'state','openclaw.json'), patchPath=path.join(temporary,'model-patch.json');
      await fs.mkdir(path.dirname(configPath),{recursive:true});
      const initial=JSON.stringify(source,null,2);
      await fs.writeFile(configPath,initial);
      await fs.writeFile(patchPath,JSON.stringify(patch.options.raw));
      const result=spawnSync(process.execPath,[path.join(nativeRoot,'openclaw.mjs'),'config','patch','--file',patchPath,
        '--replace-path','models.providers.fixture.models','--dry-run'],{cwd:temporary,encoding:'utf8',timeout:90000,
        env:{...process.env,OPENCLAW_STATE_DIR:path.dirname(configPath),OPENCLAW_CONFIG_PATH:configPath,OPENCLAW_NO_RESPAWN:'1'}});
      assert.equal(result.status,0,`${result.error || ''}\n${result.stdout}\n${result.stderr}`);
      assert.equal(await fs.readFile(configPath,'utf8'),initial,'native dry run must not mutate even the temporary fixture');
      findings.push('native CLI config.patch dry run accepts the product patch in a temporary Chinese path with spaces and a different working directory');
    } finally {await fs.rm(temporary,{recursive:true,force:true});}
  }
  const installed=JSON.parse(await fs.readFile(path.join(nativeRoot,'package.json'),'utf8'));
  return {passed:findings.length,nativeVersion:installed.version,offline:true,privateConfigTouched:false,findings};
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const report=await verifyModelSchema({cli:true});
  const output=path.join(root,'.artifacts/model-settings-verification');
  await fs.mkdir(output,{recursive:true});
  await fs.writeFile(path.join(output,'native-schema.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
}
