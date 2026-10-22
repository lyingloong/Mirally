const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function fixture() {
  const messages = [], results = [], errors = [], events = {};
  const pane = {state: {sessionKey: 'first'}};
  const window = {__mirallyIntake: {active: () => pane}, chrome: {webview: {
    postMessage: value => messages.push(value)}}, addEventListener(){}};
  window.top=window;window.location={origin:'http://127.0.0.1:18789'};
  vm.runInNewContext(fs.readFileSync('mirally/assets/control-ui-context.js', 'utf8').replace('__MIRALLY_ORIGIN__','"http://127.0.0.1:18789"'), {
    window, location:window.location, crypto: {randomUUID: () => 'nonce'}, setTimeout: () => 1, clearTimeout() {},
    File, atob, Uint8Array, Promise, Error,
  });
  const handle = {dataset: {}, setPointerCapture() {}, hasPointerCapture: () => true,
    releasePointerCapture() {}, addEventListener: (name, handler) => {events[name] = handler;},
    removeEventListener() {}};
  window.__mirallyContext.bind(handle, {onReady: value => results.push(value), onError: value => errors.push(value)});
  const event = (x = 0) => ({isPrimary: true, button: 0, pointerId: 1, clientX: x,
    clientY: 0, preventDefault() {}});
  return {window, pane, events, event, messages, results, errors};
}

test('a click or a short movement never requests any screenshot', async () => {
  const f = fixture();
  f.events.pointerdown(f.event());
  f.events.pointermove(f.event(7));
  await f.events.pointerup(f.event(7));
  assert.equal(f.messages.length, 0);
});

test('only a matching outstanding gesture accepts material', async () => {
  const f = fixture();
  f.events.pointerdown(f.event());
  f.events.pointermove(f.event(8));
  const done = f.events.pointerup(f.event(8));
  assert.equal(f.messages.length, 1);
  f.window.__mirallyContext.receive({requestId: 'unknown', dataUrl: 'data:image/png;base64,YQ=='});
  assert.equal(f.results.length, 0);
  f.window.__mirallyContext.receive({requestId: 'nonce', dataUrl: 'data:image/png;base64,YQ==', title: 'test'});
  await done;
  assert.equal(f.results.length, 1);
  assert.equal(f.results[0].file.type, 'image/png');
});

test('switching session while the capture runs rejects rather than attaches it', async () => {
  const f = fixture();
  f.events.pointerdown(f.event());
  f.events.pointermove(f.event(8));
  const done = f.events.pointerup(f.event(8));
  f.pane.state.sessionKey = 'second';
  f.window.__mirallyContext.receive({requestId: 'nonce', dataUrl: 'data:image/png;base64,YQ=='});
  await done;
  assert.equal(f.results.length, 0);
  assert.equal(f.errors.length, 1);
});

test('moving with Alt or making another gesture during a capture does not recapture', async () => {
  const f = fixture();
  f.events.pointerdown({...f.event(),altKey:true});
  f.events.pointermove(f.event(10));
  await f.events.pointerup(f.event(10));
  assert.equal(f.messages.length, 0);
  f.events.pointerdown(f.event());
  f.events.pointermove(f.event(10));
  const done = f.events.pointerup(f.event(10));
  f.events.pointerdown(f.event());
  f.events.pointermove(f.event(20));
  await f.events.pointerup(f.event(20));
  assert.equal(f.messages.length, 1);
  f.window.__mirallyContext.receive({requestId:'nonce',error:'No target'});
  await done;
});
