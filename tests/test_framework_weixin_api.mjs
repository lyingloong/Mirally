import assert from 'node:assert/strict';
import {test} from 'node:test';
import http from 'node:http';
import {WeixinApi, WeixinApiError, validateWeixinBaseUrl, WEIXIN_PROTOCOL_REFERENCE}
  from '../kernel/dsh/packages/mirally/mirally-product/src/channels/weixin-api.mjs';

const qr = {qrcode: 'scan-token', qrcode_img_content: 'https://liteapp.weixin.qq.com/q/test'};
const send = {token: 'fixture-bot-secret', to: 'fixture-user', text: '第一行\n第二行', contextToken: 'fixture-context', clientId: 'stable-id'};
const json = value => new Response(JSON.stringify(value), {headers: {'content-type': 'application/json'}});
const apiReturning = value => new WeixinApi({fetch: async () => json(value)});
const safeError = (err, expected = {}) => {
  assert.ok(err instanceof WeixinApiError);
  assert.doesNotMatch(String(err), /fixture-bot-secret|fixture-context|scan-token|verify-secret|evil\.invalid/);
  for (const [key, value] of Object.entries(expected)) assert.equal(err[key], value);
  return true;
};

test('Weixin credential origins accept official HTTPS and only explicitly enabled literal loopback fixtures', () => {
  assert.equal(validateWeixinBaseUrl('https://ilinkai.weixin.qq.com/'), 'https://ilinkai.weixin.qq.com');
  assert.equal(validateWeixinBaseUrl('https://ilinkai-idc.weixin.qq.com'), 'https://ilinkai-idc.weixin.qq.com');
  for (const value of ['http://ilinkai.weixin.qq.com', 'https://weixin.qq.com.evil.invalid', 'https://evilweixin.qq.com',
    'https://user:secret@ilinkai.weixin.qq.com', 'https://ilinkai.weixin.qq.com:8443', 'https://ilinkai.weixin.qq.com/path',
    'https://ilinkai.weixin.qq.com?q=secret', 'https://ilinkai.weixin.qq.com#frag', 'https://ilinkai.weixin.qq.com\\@evil.invalid',
    'https://ilinkai.weixin.qq.com.', ' https://ilinkai.weixin.qq.com', 'http://127.0.0.1:3141', '//ilinkai.weixin.qq.com']) {
    assert.throws(() => validateWeixinBaseUrl(value), err => safeError(err, {kind: 'configuration'}));
  }
  assert.equal(validateWeixinBaseUrl('http://127.0.0.1:3141', {allowLoopback: true}), 'http://127.0.0.1:3141');
  assert.equal(validateWeixinBaseUrl('http://localhost:3141/', {allowLoopback: true}), 'http://localhost:3141');
  for (const value of ['http://127.1:3141', 'http://2130706433:3141', 'http://0x7f000001:3141',
    'http://[::1]:3141', 'http://localhost.evil.invalid:3141', 'https://127.0.0.1:3141', 'http://user@localhost:3141']) {
    assert.throws(() => validateWeixinBaseUrl(value, {allowLoopback: true}));
  }
});

test('Weixin fixtures exercise the real fetch wire: QR POST, poll GET, authenticated cursor POST and contextual send', async t => {
  const requests = [];
  const server = http.createServer(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    requests.push({url: req.url, method: req.method, headers: req.headers, body: Buffer.concat(chunks).toString()});
    res.setHeader('content-type', 'application/json');
    if (req.url.startsWith('/ilink/bot/get_bot_qrcode')) res.end(JSON.stringify(qr));
    else if (req.url.startsWith('/ilink/bot/get_qrcode_status')) res.end(JSON.stringify({status: 'need_verifycode'}));
    else if (req.url === '/ilink/bot/getupdates') res.end('{"ret":0,"msgs":[{"message_id":18446744073709551615,"from_user_id":"fixture-user","message_type":1,"item_list":[{"type":1,"text_item":{"text":"你好"}}]}],"get_updates_buf":"cursor-next","longpolling_timeout_ms":35000}');
    else res.end('{"ret":0,"message_id":18446744073709551614}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => {server.closeAllConnections(); return new Promise(resolve => server.close(resolve));});
  const api = new WeixinApi({baseUrl: `http://127.0.0.1:${server.address().port}`, allowLoopback: true});
  assert.deepEqual(await api.getQr({tokens: ['fixture-bot-secret']}), qr);
  assert.deepEqual(await api.pollQr({qrcode: 'scan-token&x=?', verifyCode: 'verify-secret'}), {status: 'need_verifycode'});
  const updates = await api.updates({token: send.token, cursor: 'cursor-old'});
  assert.equal(updates.msgs[0].message_id, '18446744073709551615');
  assert.equal(updates.get_updates_buf, 'cursor-next');
  assert.equal((await api.sendText(send)).message_id, '18446744073709551614');
  assert.deepEqual(requests.map(({url, method}) => [method, url]), [
    ['POST', '/ilink/bot/get_bot_qrcode?bot_type=3'],
    ['GET', '/ilink/bot/get_qrcode_status?qrcode=scan-token%26x%3D%3F&verify_code=verify-secret'],
    ['POST', '/ilink/bot/getupdates'], ['POST', '/ilink/bot/sendmessage'],
  ]);
  const [getQr, pollQr, poll, outgoing] = requests;
  assert.deepEqual(JSON.parse(getQr.body), {local_token_list: ['fixture-bot-secret']});
  assert.equal(getQr.headers.authorization, undefined);
  assert.equal(getQr.headers.authorizationtype, 'ilink_bot_token');
  for (const req of requests) {
    assert.equal(req.headers['ilink-app-id'], 'bot');
    assert.equal(req.headers['ilink-app-clientversion'], String((2 << 16) | (4 << 8) | 10));
  }
  assert.equal(pollQr.headers.authorization, undefined);
  assert.equal(pollQr.headers.authorizationtype, undefined);
  assert.equal(pollQr.headers['x-wechat-uin'], undefined);
  for (const req of [getQr, poll, outgoing]) {
    const uin = Buffer.from(req.headers['x-wechat-uin'], 'base64').toString();
    assert.match(uin, /^\d+$/); assert.ok(Number(uin) <= 0xffffffff);
  }
  const baseInfo = {channel_version: WEIXIN_PROTOCOL_REFERENCE.version, bot_agent: 'Mirally/0.1.0'};
  assert.deepEqual(JSON.parse(poll.body), {get_updates_buf: 'cursor-old', base_info: baseInfo});
  assert.equal(poll.headers.authorization, 'Bearer fixture-bot-secret');
  assert.deepEqual(JSON.parse(outgoing.body), {base_info: baseInfo, msg: {from_user_id: '', to_user_id: send.to,
    client_id: 'stable-id', message_type: 2, message_state: 2, context_token: 'fixture-context',
    item_list: [{type: 1, text_item: {text: send.text}}]}});
});

test('Weixin QR confirmation requires identity, validates IDC and does not issue redirected requests', async () => {
  const confirm = {status: 'confirmed', bot_token: send.token, ilink_bot_id: 'bot', ilink_user_id: send.to,
    baseurl: 'https://ilinkai-idc.weixin.qq.com/'};
  assert.deepEqual(await apiReturning(confirm).pollQr({qrcode: qr.qrcode}), {...confirm, baseurl: 'https://ilinkai-idc.weixin.qq.com'});
  assert.deepEqual(await apiReturning({status: 'scaned_but_redirect', redirect_host: 'ilinkai-idc.weixin.qq.com'}).pollQr({qrcode: qr.qrcode}),
    {status: 'scaned_but_redirect', redirect_host: 'ilinkai-idc.weixin.qq.com'});
  for (const result of [{...confirm, baseurl: 'https://evil.invalid'}, {...confirm, bot_token: undefined},
    {...confirm, ilink_user_id: undefined}, {status: 'scaned_but_redirect', redirect_host: 'evil.invalid'},
    {status: 'scaned_but_redirect', redirect_host: 'ilinkai.weixin.qq.com/path'}, {status: 'scaned_but_redirect'},
    {status: 'unexpected'}]) {
    await assert.rejects(apiReturning(result).pollQr({qrcode: qr.qrcode}), err => safeError(err));
  }
  for (const status of ['wait', 'scaned', 'expired', 'need_verifycode', 'verify_code_blocked', 'binded_redirect']) {
    assert.deepEqual(await apiReturning({status}).pollQr({qrcode: qr.qrcode}), {status});
  }
});

test('Weixin QR payload is bounded inert content, rejecting markup and executable or foreign URLs', async () => {
  for (const value of ['javascript:alert(1)', 'data:image/svg+xml;base64,PHN2Zz4=', 'file:///secret',
    '<svg onload=evil>', '//evil.invalid', 'https://evil.invalid/scan', 'https://user@liteapp.weixin.qq.com/q',
    'https://liteapp.weixin.qq.com:8443/q', 'x'.repeat(8193), 'bad\nvalue']) {
    await assert.rejects(apiReturning({...qr, qrcode_img_content: value}).getQr(), err => safeError(err));
  }
  assert.deepEqual(await apiReturning({...qr, qrcode_img_content: 'opaque-scan-payload'}).getQr(),
    {...qr, qrcode_img_content: 'opaque-scan-payload'});
});

test('Weixin business failures flag stale credentials without including provider error descriptions', async () => {
  for (const response of [{ret: -14}, {ret: 8, errcode: -14}]) {
    const api = apiReturning({...response, errmsg: `fixture-bot-secret ${send.contextToken}`});
    await assert.rejects(api.updates({token: send.token}), err => safeError(err, {code: -14, credentialStale: true, kind: 'business'}));
    await assert.rejects(api.sendText(send), err => safeError(err, {code: -14, deliveryState: 'rejected'}));
  }
  await assert.rejects(apiReturning({ret: 5}).sendText(send), err => safeError(err, {code: 5, deliveryState: 'rejected'}));
});

test('Weixin rejects HTTP redirects without forwarding credentials or following Location', async () => {
  let calls = 0;
  const api = new WeixinApi({fetch: async (url, options) => {
    calls++; assert.equal(options.redirect, 'manual');
    return new Response('fixture-bot-secret', {status: 307, headers: {location: 'https://evil.invalid/?scan-token'}});
  }});
  await assert.rejects(api.sendText(send), err => safeError(err, {code: 'redirect', deliveryState: 'unknown'}));
  assert.equal(calls, 1);
});

test('Weixin send transport failures remain unknown and are never retried', async () => {
  let calls = 0;
  const api = new WeixinApi({fetch: async () => {calls++; throw Error('https://evil.invalid/?scan-token fixture-bot-secret');}});
  await assert.rejects(api.sendText(send), err => safeError(err, {code: 'network', deliveryState: 'unknown'}));
  assert.equal(calls, 1);
  const failed = new WeixinApi({fetch: async () => new Response('fixture-bot-secret', {status: 502})});
  await assert.rejects(failed.sendText(send), err => safeError(err, {httpStatus: 502, deliveryState: 'unknown'}));
  await assert.rejects(apiReturning({}).sendText(send), err => safeError(err, {code: 'schema', deliveryState: 'unknown'}));
  await assert.rejects(apiReturning({message_id: {unexpected: 'fixture-bot-secret'}}).sendText(send),
    err => safeError(err, {code: 'schema', deliveryState: 'unknown'}));
  await assert.rejects(apiReturning({ret: 0, message_id: ''}).sendText(send),
    err => safeError(err, {code: 'schema', deliveryState: 'unknown'}));
});

test('Weixin bounds declared and streamed response sizes and leaves invalid sends unknown', async () => {
  for (const fetch of [
    async () => new Response('{}', {headers: {'content-length': String(1024 * 1024 + 1)}}),
    async () => new Response('汉'.repeat(400000)),
    async () => new Response('not-json fixture-bot-secret'),
    async () => json([]), async () => json({ret: '0'}),
  ]) {
    await assert.rejects(new WeixinApi({fetch}).sendText(send), err => safeError(err, {code: 'schema', deliveryState: 'unknown'}));
  }
});

test('Weixin timeouts cover both fetch and slow response body and cancel the owned signal', async () => {
  let observed;
  const hangingFetch = new WeixinApi({requestTimeoutMs: 20, fetch: (url, options) => {
    observed = options.signal; return new Promise(() => {});
  }});
  await assert.rejects(hangingFetch.sendText(send), err => safeError(err, {kind: 'timeout', deliveryState: 'unknown'}));
  assert.equal(observed.aborted, true);
  const slowBody = new WeixinApi({requestTimeoutMs: 20, fetch: async (url, options) => {
    observed = options.signal;
    return new Response(new ReadableStream({start(controller) {
      options.signal.addEventListener('abort', () => controller.error(Error('fixture-bot-secret')), {once: true});
      controller.enqueue(new TextEncoder().encode('{"ret":'));
    }}));
  }});
  await assert.rejects(slowBody.sendText(send), err => safeError(err, {kind: 'timeout', deliveryState: 'unknown'}));
  assert.equal(observed.aborted, true);
});

test('Weixin external abort cancels polling promptly; pre-aborted sends never reach fetch', async () => {
  let calls = 0, requestSignal;
  const api = new WeixinApi({fetch: (url, options) => {
    calls++; requestSignal = options.signal; return new Promise(() => {});
  }});
  const controller = new AbortController();
  const pending = api.updates({token: send.token, signal: controller.signal});
  controller.abort(Error('fixture-bot-secret'));
  await assert.rejects(pending, err => safeError(err, {kind: 'aborted'}));
  assert.equal(requestSignal.aborted, true);
  await assert.rejects(api.sendText({...send, signal: controller.signal}), err => safeError(err, {kind: 'aborted', deliveryState: 'rejected'}));
  assert.equal(calls, 1);
});

test('Weixin update validation keeps cursor ownership with the caller and rejects malformed messages', async () => {
  assert.deepEqual(await apiReturning({ret: 0}).updates({token: send.token, cursor: 'keep'}), {ret: 0, msgs: []});
  for (const value of [{}, {msgs: 'no'}, {msgs: [null]}, {msgs: [{message_type: '1'}]},
    {msgs: [{from_user_id: 5}]}, {msgs: [{item_list: [1]}]}, {msgs: [{item_list: [{type: 1, text_item: {text: 5}}]}]},
    {ret: 0, get_updates_buf: 5}, {ret: 0, longpolling_timeout_ms: -1}]) {
    await assert.rejects(apiReturning(value).updates({token: send.token}), err => safeError(err, {kind: 'schema'}));
  }
});

test('Weixin input validation happens before network and accepts explicit client identity metadata', async () => {
  let calls = 0;
  const api = new WeixinApi({fetch: async () => {calls++; return json({ret: 0});}});
  for (const args of [{...send, token: ''}, {...send, clientId: ''}, {...send, contextToken: undefined},
    {...send, text: ''}, {...send, text: '汉'.repeat(6000)}, {...send, to: 'bad\r\nvalue'}]) {
    await assert.rejects(api.sendText(args), err => safeError(err));
  }
  await assert.rejects(api.getQr({tokens: Array(11).fill('token')}));
  assert.equal(calls, 0);
  const custom = new WeixinApi({version: '1.2.3', botAgent: 'Mirally/1.2 (desktop)', fetch: async (url, options) => {
    assert.equal(options.headers['iLink-App-ClientVersion'], '66051');
    assert.deepEqual(JSON.parse(options.body).base_info, {channel_version: '1.2.3', bot_agent: 'Mirally/1.2 (desktop)'});
    return json({ret: 0});
  }});
  await custom.sendText(send);
  for (const options of [{version: '1.2.999'}, {version: '1.2'}, {botAgent: 'bad\r\nvalue'}, {pollTimeoutMs: 0}]) {
    assert.throws(() => new WeixinApi(options));
  }
});
