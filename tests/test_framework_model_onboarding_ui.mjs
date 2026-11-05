/** Exercise the retained model onboarding renderers without a host or model. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';

const requireUi = createRequire(new URL('../frontend/control-ui/package.json', import.meta.url));
const ts = requireUi('typescript');
const html = (strings, ...values) => ({strings, values});
const nothing = Symbol('nothing');
const compiled = new Map();
const stringify = value => value == null || typeof value === 'symbol' || typeof value === 'function' ? ''
  : Array.isArray(value) ? value.map(stringify).join('') : value?.strings
    ? value.strings.map((part, index) => part + stringify(value.values[index])).join('') : String(value);

function load(path, names, sandbox = {}) {
  if (!compiled.has(path)) {
    const source = readFileSync(new URL(`../frontend/control-ui/ui/src/pages/chat/${path}`, import.meta.url), 'utf8');
    compiled.set(path, ts.transpileModule(source, {compilerOptions: {
      target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
    }}).outputText.replace(/^import[\s\S]*?;\s*/gm, '').replace(/^export /gm, ''));
  }
  const context = {html, nothing, t: key => key, ...sandbox};
  vm.runInNewContext(compiled.get(path) + `\nglobalThis.api = {${names.join(',')}};`, context);
  return context.api;
}

function templates(value, result = []) {
  if (Array.isArray(value)) value.forEach(item => templates(item, result));
  else if (value?.strings) {result.push(value); value.values.forEach(item => templates(item, result));}
  return result;
}

function composer(banner) {
  const noContent = () => nothing;
  const {renderChatComposerView} = load('components/chat-composer-view.ts', ['renderChatComposerView'], {
    paneDomId: (pane, name) => pane + '-' + name,
    detectTextDirection: () => 'auto', renderChatRunStatusIndicator: noContent,
    renderFallbackIndicator: noContent, renderChatQueue: noContent,
    renderComposerQuestionDock: noContent, renderChatGoalRecovery: noContent,
  });
  return renderChatComposerView({props: {paneId: 'one', connected: true, disabledBanner: banner},
    state: {}, visibleDraft: '', showComposer: false});
}

function thread(projection, {modelSetupRequired = true, onModelSetup} = {}) {
  const actualWelcome = load('components/chat-welcome.ts', ['renderWelcomeState'], {
    registerCommandPaletteEnglish() {},
  }).renderWelcomeState;
  const welcomeProps = [];
  const {renderChatThread} = load('components/chat-thread.ts', ['renderChatThread'], {
    ref: () => nothing, markdownBlocks: () => nothing, linkReaderPrefetch: () => nothing,
    renderChatPositionRail: () => nothing, renderLoadingState: () => nothing,
    renderPanelLoadingSkeleton: () => nothing,
    projectChatTranscript: () => ({positionIndex: [], isDirectThread: true,
      isEmpty: true, showLoadingSkeleton: false, searchOpen: false,
      renderRows: () => html`<article>history</article>`, ...projection}),
    renderWelcomeState: props => {
      welcomeProps.push(props);
      return props.modelSetupRequired ? actualWelcome(props) : html`<div>ordinary welcome</div>`;
    },
  });
  const transcript = {entryAnimations: {sync() {}}, scrollElementRef() {}};
  return {rendered: renderChatThread({paneId: 'one', sessionKey: 'agent:main:one',
    modelSetupRequired, onModelSetup}, {renderSession: (_pane, _key, render) => render(transcript)}), welcomeProps};
}

test('model recovery banner retains its action and carries only its requested message', () => {
  const translated = [], onAction = () => {};
  const {createChatModelSetupBanner} = load('chat-model-setup.ts', ['createChatModelSetupBanner'], {
    t: key => {translated.push(key); return key;},
  });
  const banner = createChatModelSetupBanner(onAction, 'model unavailable');
  assert.equal(banner.text, 'model unavailable');
  assert.equal(banner.onAction, onAction);
  assert.equal(banner.kind, 'above-composer');
  assert.equal(banner.hideWhenModelSetupWelcome, true);
  assert.equal(translated.includes('modelSetup.commandHint'), false);
  const rendered = composer(banner);
  const marker = templates(rendered).find(item => item.strings.some(part => part.includes('?data-model-setup=')));
  assert.ok(marker);
  assert.equal(marker.values[marker.strings.findIndex(part => part.includes('?data-model-setup='))], true);
  assert.ok(stringify(rendered).includes('model unavailable'));
});

test('ordinary recovery banners are not marked for setup-only suppression', () => {
  const rendered = composer({kind: 'above-composer', text: 'reconnect', actionLabel: 'retry', onAction() {}});
  const marker = templates(rendered).find(item => item.strings.some(part => part.includes('?data-model-setup=')));
  assert.ok(marker);
  assert.equal(marker.values[marker.strings.findIndex(part => part.includes('?data-model-setup='))], false);
});

test('the real empty model welcome owns a working connection action', () => {
  let opened = 0;
  const onModelSetup = () => {opened++;};
  const {rendered, welcomeProps} = thread({}, {onModelSetup});
  assert.equal(welcomeProps.length, 1);
  assert.equal(welcomeProps[0].onModelSetup, onModelSetup);
  const button = templates(rendered).find(item => item.strings.some(part => part.includes('<button')));
  assert.ok(button);
  const activate = button.values[button.strings.findIndex(part => part.includes('@click='))];
  assert.equal(activate, onModelSetup);
  activate();
  assert.equal(opened, 1);
});

test('history, search and loading do not create a setup welcome; ordinary welcome keeps its behavior', () => {
  for (const projection of [{isEmpty: false}, {searchOpen: true},
    {isEmpty: false, showLoadingSkeleton: true}]) {
    const result = thread(projection, {onModelSetup() {}});
    assert.equal(result.welcomeProps.length, 0);
    assert.doesNotMatch(stringify(result.rendered), /agent-chat__welcome--setup/);
  }
  const result = thread({}, {modelSetupRequired: false, onModelSetup() {}});
  assert.equal(result.welcomeProps.length, 1);
  assert.equal(result.welcomeProps[0].onModelSetup, undefined);
});


test('welcome suggestions fill an empty draft and focus without sending', () => {
  let draft = '', focused = 0, sent = 0;
  const {renderWelcomeSuggestions} = load('components/chat-welcome.ts', ['renderWelcomeSuggestions'], {
    registerCommandPaletteEnglish() {},
  });
  const rendered = renderWelcomeSuggestions({getDraft: () => draft,
    onDraftChange: value => {draft = value;}, onFocusComposer: () => {focused++;},
    onSend: () => {sent++;}});
  const button = templates(rendered).find(item => item.strings.some(part => part.includes('<button')));
  const activate = button.values[button.strings.findIndex(part => part.includes('@click='))];
  activate();
  assert.equal(draft, 'chat.welcome.suggestions.whatCanYouDo');
  assert.equal(focused, 1);
  assert.equal(sent, 0);
});

test('a delayed welcome suggestion preserves newer input including whitespace', () => {
  let draft = '', writes = 0, focused = 0;
  const {renderWelcomeSuggestions} = load('components/chat-welcome.ts', ['renderWelcomeSuggestions'], {
    registerCommandPaletteEnglish() {},
  });
  const rendered = renderWelcomeSuggestions({draft: '', getDraft: () => draft,
    onDraftChange: () => {writes++;}, onFocusComposer: () => {focused++;}});
  const button = templates(rendered).find(item => item.strings.some(part => part.includes('<button')));
  const activate = button.values[button.strings.findIndex(part => part.includes('@click='))];
  for (const next of ['用户刚写的草稿', '  ']) {
    draft = next;
    activate();
    assert.equal(draft, next);
  }
  assert.equal(writes, 0);
  assert.equal(focused, 2);
});

test('named new-conversation link keeps native modified navigation and permission rejection', () => {
  const {renderNewSessionLink} = load('../../components/new-session-link.ts', ['renderNewSessionLink'], {
    pathForRoute: () => '/new', newSessionSearch: () => '?agent=main', icons: {plus: '+'},
    shouldHandleNavigationClick: event => !event.ctrlKey,
    t: key => key,
  });
  let opened = 0, prevented = 0;
  const params = {basePath: '', agentId: 'main', className: 'sidebar-new-conversation',
    label: '新对话', showLabel: true, onOpen: () => {opened++;}};
  const rendered = renderNewSessionLink(params);
  assert.match(stringify(rendered), /新对话/);
  assert.match(stringify(rendered), /href=\/new\?agent=main/);
  const activate = rendered.values[rendered.strings.findIndex(part => part.includes('@click='))];
  activate({ctrlKey: true, preventDefault() {prevented++;}});
  assert.equal(opened, 0);
  assert.equal(prevented, 0);
  activate({ctrlKey: false, preventDefault() {prevented++;}});
  assert.equal(opened, 1);
  const disabled = renderNewSessionLink({...params, disabledReason: 'not allowed'});
  const reject = disabled.values[disabled.strings.findIndex(part => part.includes('@click='))];
  reject({ctrlKey: true, preventDefault() {prevented++;}});
  assert.equal(opened, 1);
  assert.equal(prevented, 2);
  assert.match(stringify(disabled), /aria-disabled=true/);
  assert.doesNotMatch(stringify(disabled), /href=\/new/);
});
