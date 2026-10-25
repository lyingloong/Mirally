import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {modelListing, discoverProductModels} from '../kernel/dsh/packages/mirally/mirally-product/src/model-probe.mjs';
import {MirallyProduct} from '../kernel/dsh/packages/mirally/mirally-product/src/index.mjs';

const source = stripTypeScriptTypes(readFileSync(new URL('../frontend/control-ui/ui/src/pages/mirally/model-settings.ts', import.meta.url), 'utf8'), {mode: 'transform'});
const {modelDraft, deepSeekPreset, adoptDiscoveredModel, isDeepSeekEndpoint, modelCapabilityText} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

test('model draft preserves legacy reasoning and protocol until the user changes them', () => {
  assert.equal(modelDraft({reasoning_enabled: false}).reasoning_effort, 'off');
  assert.equal(modelDraft({reasoning_enabled: true}).reasoning_effort, 'medium');
  assert.equal(modelDraft({base_url: 'https://api.deepseek.com', reasoning_enabled: true}).reasoning_effort, 'high');
  assert.equal(modelDraft({reasoning_enabled: false, reasoning_effort: 'default'}).reasoning_effort, 'default');
  assert.equal(modelDraft({api_format: 'openai-responses'}).api_format, 'openai-responses');
  assert.equal(isDeepSeekEndpoint('https://api.deepseek.com.evil.test'), false);
  assert.equal(isDeepSeekEndpoint('bad'), false);
  const draft = modelDraft({api_key: 'private', base_url: 'https://own.test', model_id: 'own', api_format: 'openai-chat'});
  assert.equal(draft.base_url, 'https://own.test');
  assert.equal(Object.hasOwn(draft, 'api_key'), false);
  assert.deepEqual({...deepSeekPreset(draft), context_window: undefined, max_tokens: undefined, temperature: undefined}, {
    provider: 'deepseek', base_url: 'https://api.deepseek.com', model_id: 'deepseek-flash', api_format: 'openai-responses',
    image_enabled: true, reasoning_effort: 'default', context_window: undefined, max_tokens: undefined, temperature: undefined,
  });
});

test('discovered metadata is advisory, bounded, and never guesses absent capabilities', () => {
  const models = modelListing({data: [
    {id: 'flash', input_modalities: ['text', 'image'], context_window: 1000000, max_output_tokens: 8192,
      effort: {supported_levels: ['low', 'high', 'max'], default_level: 'high'}},
    {id: 'pro', input_modalities: ['text']}, {id: 'plain'}, {id: 'flash'}, {id: ''}, {name: 'No ID'},
  ]});
  assert.equal(models.length, 3);
  assert.deepEqual(models[0], {id: 'flash', name: 'flash', input_modalities: ['text', 'image'], context_window: 1000000,
    max_output_tokens: 8192, effort: {supported_levels: ['low', 'high', 'max'], default_level: 'high'}});
  const draft = modelDraft({model_id: 'prior', max_tokens: 12000, image_enabled: true, reasoning_effort: 'medium'});
  const flash = adoptDiscoveredModel(draft, models[0]);
  assert.equal(flash.context_window, '1000000'); assert.equal(flash.max_tokens, '8192');
  assert.equal(flash.image_enabled, true); assert.equal(flash.reasoning_effort, 'default');
  assert.equal(adoptDiscoveredModel(draft, models[1]).image_enabled, false);
  assert.equal(adoptDiscoveredModel(draft, models[2]).image_enabled, true);
  assert.match(modelCapabilityText(models[0]), /1,000,000/);
  assert.match(modelCapabilityText(models[2]), /未声明/);
  assert.throws(() => modelListing({secret: 'not a list'}), /有效的模型列表/);
});

async function endpoint(t, handler) {
  const server = createServer(handler);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {server.closeAllConnections(); await new Promise(resolve => server.close(resolve));});
  return `http://127.0.0.1:${server.address().port}`;
}

test('model discovery reads real HTTP metadata using the saved credential and preserves gateway prefix', async t => {
  const seen = [];
  const url = await endpoint(t, (req, res) => {
    seen.push({path: req.url, auth: req.headers.authorization});
    res.setHeader('content-type', 'application/json');
    res.end(JSON.stringify({data: [{id: 'own', input_modalities: ['text', 'image'], effort: {supported_levels: ['low', 'max']}}]}));
  });
  const reply = await discoverProductModels({base_url: url + '/gateway/v1/', api_format: 'openai-responses', api_key: 'saved-key'}, AbortSignal.timeout(1000));
  assert.deepEqual(seen, [{path: '/gateway/v1/models', auth: 'Bearer saved-key'}]);
  assert.deepEqual(reply.models[0].input_modalities, ['text', 'image']);
  assert.equal(JSON.stringify(reply).includes('saved-key'), false);
});

test('model discovery refuses redirects, bounds bodies, and gives actionable auth and listing failures', async t => {
  let redirected = false;
  const url = await endpoint(t, (req, res) => {
    if(req.url === '/redirect/models') {res.writeHead(302, {location: '/target'}); return res.end();}
    if(req.url === '/target') redirected = true;
    if(req.url === '/large/models') return res.end('x'.repeat(2 * 1024 * 1024 + 1));
    if(req.url === '/missing/models') {res.writeHead(404); return res.end('sensitive server body');}
    res.writeHead(401); res.end('private error content');
  });
  const get = suffix => discoverProductModels({base_url: url + suffix, api_format: 'openai-chat', api_key: 'private'}, AbortSignal.timeout(2000));
  await assert.rejects(get('/redirect'), /无法连接/);
  assert.equal(redirected, false);
  await assert.rejects(get('/large'), /2 MiB/);
  await assert.rejects(get('/missing'), /手动填写/);
  await assert.rejects(get(''), /HTTP 401.*API Key/);
  await assert.rejects(discoverProductModels({base_url: url + '?key=private'}), /查询参数/);
});

test('Anthropic listing uses its own protocol headers and does not send bearer auth', async t => {
  const url = await endpoint(t, (req,res) => {
    assert.equal(req.url, '/gateway/v1/models');
    assert.equal(req.headers['x-api-key'], 'own');
    assert.equal(req.headers.authorization, undefined);
    assert.equal(req.headers['anthropic-version'], '2023-06-01');
    res.end('{"data":[]}');
  });
  assert.deepEqual((await discoverProductModels({base_url:url+'/gateway/v1',api_format:'anthropic-messages',api_key:'own'},AbortSignal.timeout(2000))).models, []);
});

test('model discovery maps cancellation and disconnect after headers to friendly bounded failures', async t => {
  let sentHeaders;
  const headersSent = new Promise(resolve => {sentHeaders=resolve;});
  const url = await endpoint(t, (req,res) => {
    res.writeHead(200,{'content-type':'application/json'});
    res.write('{"data":[');
    if(req.url==='/stall/models')sentHeaders();
    else setTimeout(()=>res.destroy(),30);
  });
  const controller=new AbortController();
  const stalled=discoverProductModels({base_url:url+'/stall',api_format:'openai-chat'},controller.signal);
  await headersSent;
  const timeout=setTimeout(()=>controller.abort(new DOMException('fixture body timeout','TimeoutError')),30);
  try {
    await assert.rejects(stalled,error=>error.status===400&&error.code==='model_discovery_timeout'&&/取消或超时/.test(error.message));
  } finally {clearTimeout(timeout);}
  await assert.rejects(discoverProductModels({base_url:url+'/disconnect',api_format:'openai-chat'},AbortSignal.timeout(2000)),
    error=>error.status===400&&error.code==='model_discovery_failed'&&/读取中断/.test(error.message));
});

test('settings probe uses the real product route and auxiliary chain without admitting a conversation', async t => {
  const calls = [], product = Object.create(MirallyProduct.prototype);
  Object.assign(product, {ready:true,config:{testMode:false},runEffect:(_id,action)=>action(new AbortController().signal),
    verifyOrigin(){}, authorized(){return true;}, async modelCall(input){calls.push(input);return {text:'Mirally connection OK',model:'own',usage:{outputTokens:4}};}});
  const url = await endpoint(t,(req,res)=>void product.route(req,res).catch(error=>{res.writeHead(error.status??500);res.end(error.message);}));
  product.url=url;
  product.server={address:()=>({port:Number(new URL(url).port)})};
  const result = await (await fetch(url+'/api/models/test',{method:'POST',body:'{}'})).json();
  assert.equal(result.scope,'text_generation');assert.equal(result.text,'Mirally connection OK');
  assert.equal(calls.length,1);assert.equal(calls[0].max_tokens,512);assert.match(calls[0].prompt,/Mirally connection OK/);
  const rejected = await fetch(url+'/api/models/test',{method:'POST',body:JSON.stringify({api_key:'must-not-use'})});
  assert.equal(rejected.status,400);assert.equal(calls.length,1);
});

test('auxiliary calls reject in-band native stream failures, including partial responses', async () => {
  const product=Object.create(MirallyProduct.prototype);
  let finish={kind:'error',failure:{message:'provider temporarily unavailable',code:'PROVIDER_UNAVAILABLE'}};
  Object.assign(product,{async refreshModels(){},models:{requireOptions:()=>({provider:'fixture',model:'own'})},ctx:{llm:{async prepareCall(){return {
    config:{},async *stream(){yield {type:'text-delta',text:'partial answer'};if(finish)yield {type:'finish',reason:finish};},
  };}}}});
  await assert.rejects(product.modelCall({prompt:'test'}),error=>error.status===502&&error.code==='PROVIDER_UNAVAILABLE');
  finish={kind:'aborted',failure:{message:'cancelled',code:'ABORTED'}};
  await assert.rejects(product.modelCall({prompt:'test'}),error=>error.status===499&&error.code==='ABORTED');
  finish=undefined;
  await assert.rejects(product.modelCall({prompt:'test'}),error=>error.code==='model_request_failed');
  finish={kind:'stop'};assert.equal((await product.modelCall({prompt:'test'})).text,'partial answer');
});
