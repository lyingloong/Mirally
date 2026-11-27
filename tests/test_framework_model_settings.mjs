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
    {id: 'named', display_name: '优先显示名', name: '次选名'},
  ]});
  assert.equal(models.length, 4);
  assert.deepEqual(models[0], {id: 'flash', name: 'flash', input_modalities: ['text', 'image'], context_window: 1000000,
    max_output_tokens: 8192, effort: {supported_levels: ['low', 'high', 'max'], default_level: 'high'}});
  assert.equal(models[3].name, '优先显示名');
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
    assert.equal(req.url, '/gateway/v1/models?limit=1000');
    assert.equal(req.headers['x-api-key'], 'own');
    assert.equal(req.headers.authorization, undefined);
    assert.equal(req.headers['anthropic-version'], '2023-06-01');
    res.end('{"data":[]}');
  });
  assert.deepEqual((await discoverProductModels({base_url:url+'/gateway/v1',api_format:'anthropic-messages',api_key:'own'},AbortSignal.timeout(2000))).models, []);
});

test('Anthropic discovery pages with limit/after_id and prefers the service-advertised display name', async t => {
  const seen = [];
  const url = await endpoint(t, (req, res) => {
    seen.push({path: req.url, key: req.headers['x-api-key'] ?? null, auth: req.headers.authorization ?? null, version: req.headers['anthropic-version'] ?? null});
    const after = new URL(req.url, 'http://fixture.test').searchParams.get('after_id');
    const rows = after === null
      ? Array.from({length: 20}, (_, index) => ({id: `model-${index + 1}`, display_name: `模型 ${index + 1}`}))
      : Array.from({length: 5}, (_, index) => ({id: `model-${21 + index}`, name: `relay ${21 + index}`}));
    res.end(JSON.stringify({data: rows, has_more: after === null, last_id: after === null ? 'model-20' : null}));
  });
  const reply = await discoverProductModels({base_url: url + '/gateway/v1', api_format: 'anthropic-messages', api_key: 'own'}, AbortSignal.timeout(5000));
  assert.deepEqual(seen.map(item => item.path), ['/gateway/v1/models?limit=1000', '/gateway/v1/models?limit=1000&after_id=model-20']);
  assert.ok(seen.every(item => item.key === 'own' && item.auth === null && item.version === '2023-06-01'));
  assert.equal(reply.truncated, false);
  assert.equal(reply.models.length, 25);
  assert.equal(reply.models[0].name, '模型 1');
  assert.equal(reply.models[20].name, 'relay 21');
  assert.equal(reply.models[24].id, 'model-25');
});

test('Anthropic discovery bounds runaway pagination to ten bounded pages', async t => {
  let requests = 0;
  const url = await endpoint(t, (req, res) => {
    requests++;
    res.end(JSON.stringify({data: Array.from({length: 20}, (_, index) => ({id: `m-${requests}-${index}`})), has_more: true, last_id: 'more'}));
  });
  const reply = await discoverProductModels({base_url: url, api_format: 'anthropic-messages', api_key: 'own'}, AbortSignal.timeout(10000));
  assert.equal(requests, 10);
  assert.equal(reply.models.length, 200);
  assert.equal(reply.truncated, true);
});

test('Anthropic discovery stops at the row cap without spending another request', async t => {
  let requests = 0;
  const url = await endpoint(t, (req, res) => {
    requests++;
    res.end(JSON.stringify({data: Array.from({length: 1000}, (_, index) => ({id: `m-${index}`})), has_more: true, last_id: 'm-999'}));
  });
  const reply = await discoverProductModels({base_url: url, api_format: 'anthropic-messages', api_key: 'own'}, AbortSignal.timeout(10000));
  assert.equal(requests, 1);
  assert.equal(reply.models.length, 1000);
  assert.equal(reply.truncated, true);
});

test('Anthropic discovery falls back once to the bare listing when a gateway rejects pagination parameters', async t => {
  const seen = [];
  const url = await endpoint(t, (req, res) => {
    seen.push(req.url);
    if (req.url.includes('limit=')) {res.writeHead(400); return res.end('unknown query parameter');}
    res.end(JSON.stringify({data: [{id: 'solo', display_name: 'Solo 模型'}, {id: 'solo'}]}));
  });
  const reply = await discoverProductModels({base_url: url, api_format: 'anthropic-messages', api_key: 'own'}, AbortSignal.timeout(5000));
  assert.deepEqual(seen, ['/v1/models?limit=1000', '/v1/models']);
  assert.deepEqual(reply.models, [{id: 'solo', name: 'Solo 模型'}]);
  assert.equal(reply.truncated, false);
});

test('Anthropic pagination and fallback failures stay actionable instead of returning a partial list', async t => {
  const paths = [];
  const url = await endpoint(t, (req, res) => {
    paths.push(req.url);
    if (req.url.startsWith('/midfail') && !req.url.includes('after_id=')) {
      res.end(JSON.stringify({data: [{id: 'first'}], has_more: true, last_id: 'first'})); return;
    }
    if (req.url.startsWith('/badjson') && !req.url.includes('after_id=')) {
      res.end(JSON.stringify({data: [{id: 'first'}], has_more: true, last_id: 'first'})); return;
    }
    if (req.url.startsWith('/fallbackreject') && req.url.includes('limit=')) {res.writeHead(422); return res.end('unknown parameter');}
    if (req.url.includes('after_id=') && req.url.startsWith('/badjson')) return res.end('not json at all');
    res.writeHead(401); res.end('private error content');
  });
  const get = suffix => discoverProductModels({base_url: url + suffix, api_format: 'anthropic-messages', api_key: 'own'}, AbortSignal.timeout(5000));
  await assert.rejects(get('/midfail'), error => error.status === 400 && error.code === 'model_discovery_failed' && /HTTP 401.*API Key/.test(error.message));
  assert.ok(paths.some(path => path.startsWith('/midfail') && path.includes('after_id=')));
  await assert.rejects(get('/badjson'), /未返回有效 JSON/);
  await assert.rejects(get('/fallbackreject'), /HTTP 401.*API Key/);
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
