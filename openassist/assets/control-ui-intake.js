(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else if (root.top === root && root.location.origin === __MIRALLY_ORIGIN__ && root.chrome?.webview) root.__mirallyIntake = api;
})(typeof window === 'object' ? window : globalThis, function (root) {
  'use strict';

  function active() {
    return [...(root.document?.querySelectorAll(
      'openclaw-chat-pane.chat-pane-cache__pane--active') ?? [])]
      .find(pane => pane.presented !== false && pane.state?.handleSendChat) ?? null;
  }

  async function ensureReady() {
    return active() ?? await root.__mirallyCompanion?.ensureReady?.() ?? null;
  }

  async function attach(files) {
    if (!files?.length) return false;
    const pane = await ensureReady();
    const input = pane?.querySelector('.agent-chat__file-input');
    if (!input || input.disabled || !root.DataTransfer) return false;
    const transfer = new root.DataTransfer();
    for (const file of files) transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new root.Event('change', {bubbles: true}));
    return true;
  }

  function readDrop(transfer) {
    const files = [...(transfer?.files ?? [])];
    if (!files.length) {
      for (const item of transfer?.items ?? []) {
        if (item.kind !== 'file') continue;
        const file = item.getAsFile?.();
        if (file) files.push(file);
      }
    }
    // A file's companion URI or text often names a local path, not extra material.
    if (files.length) return {files, urls: [], text: ''};
    const get = type => {
      try { return transfer?.getData?.(type) ?? ''; }
      catch { return ''; }
    };
    const urls = [];
    for (const line of get('text/uri-list').split(/\r?\n/)) {
      const value = line.trim();
      if (!value || value.startsWith('#')) continue;
      try {
        const url = new URL(value);
        if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) continue;
        if (!urls.includes(url.href)) urls.push(url.href);
      } catch { /* Non-URLs remain ordinary text only when explicitly supplied as text. */ }
    }
    const text = get('text/plain').trim();
    if (!urls.length && /^https?:\/\/\S+$/.test(text)) {
      try { const url=new URL(text);if(!url.username&&!url.password) urls.push(url.href); } catch { /* Keep invalid addresses as inert text. */ }
    }
    return {files, urls, text};
  }

  return {active, ensureReady, attach, readDrop};
});
