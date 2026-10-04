/* A narrow, same-origin presentation layer. No RPC, storage or message routing. */
(() => {
  if (window.top !== window || location.origin !== __MIRALLY_ORIGIN__) return;
  if (window.__mirallyPresentation) return;
  window.__mirallyPresentation = {version: __MIRALLY_VERSION__};
  const MARK = __MIRALLY_MARK__;
  const CSS = __MIRALLY_STYLE__;
  const IMAGE_SELECTOR = '.topbar-brand__logo, .login-gate__logo, img.identity-avatar--agent, .chat-avatar.assistant, .identity-avatar__image';
  const LABEL_SELECTOR = '.topbar-brand__title, .login-gate__brand-name, .agent-select__name, .settings-sidebar__item-label, .settings-theme-card__label, .picker-select__description, .settings-typography-preview__caption, .settings-section__heading, .settings-row__title, .settings-row__description, .settings-control__sr-label, .settings-select option, .assistant-panel-tab, .mount-fallback__eyebrow';
  const CONTENT_SELECTOR = '.chat-message, .chat-text, .chat-message__content, .markdown, .markdown-body, pre, code, textarea, input, [contenteditable], .plugin-card, .catalog-card';
  function isDefaultImage(image) {
    const source = image.getAttribute('src') || '';
    try {
      const url = new URL(source, location.href);
      return url.origin === location.origin && /\/(favicon(?:-\d+)?\.(?:svg|png)|apple-touch-icon\.png)$/.test(url.pathname);
    } catch { return false; }
  }
  function images(scope) {
    const candidates = [...scope.querySelectorAll?.(IMAGE_SELECTOR) || []];
    if (scope.matches?.(IMAGE_SELECTOR)) candidates.push(scope);
    for (const image of candidates) {
      // Respect user-selected agent avatars; change only the upstream fallback.
      if (isDefaultImage(image)) image.src = MARK;
    }
  }
  function labels(scope) {
    const candidates = [...scope.querySelectorAll?.(LABEL_SELECTOR) || []];
    if (scope.matches?.(LABEL_SELECTOR)) candidates.push(scope);
    for (const node of candidates) {
      if (node.closest(CONTENT_SELECTOR)) continue;
      if (node.matches('.picker-select__description') && !/\/settings\/appearance\/?$/.test(location.pathname)) continue;
      for (const child of node.childNodes) {
        if (child.nodeType !== Node.TEXT_NODE) continue;
        const text = child.textContent.trim();
        let replacement = child.textContent;
        if (/OpenClaw|Clawd|Clawdbot/.test(text)) replacement = replacement.replace(/OpenClaw(?: Control(?: UI)?)?|Clawdbot|Clawd/g, 'Mirally');
        if (node.matches('.settings-theme-card__label, .picker-select__description')) replacement = replacement.replace(/\bClaw\b/g, '晴空蓝');
        if (node.matches('.settings-section__heading, .settings-row__title, .settings-row__description, .settings-control__sr-label')) replacement = replacement.replace(/龙虾图鉴/g, '伙伴形象').replace(/龙虾/g, '伙伴');
        if (replacement !== child.textContent) child.textContent = replacement;
      }
    }
  }
  function chrome(scope) {
    images(scope);
    labels(scope);
    for (const icon of scope.querySelectorAll?.('link[rel~="icon"], link[rel="apple-touch-icon"]') || []) {
      if (icon.getAttribute('href') !== MARK) icon.href = MARK;
    }
  }
  function install() {
    if (!document.documentElement) return;
    document.documentElement.dataset.mirally = __MIRALLY_VERSION__;
    document.documentElement.style.setProperty('--mirally-mark', `url("${MARK}")`);
    if (!document.getElementById('mirally-presentation')) {
      const style = document.createElement('style');
      style.id = 'mirally-presentation';
      style.textContent = CSS;
      (document.head || document.documentElement).append(style);
    }
    const title = document.title.replace(/OpenClaw(?: Control(?: UI)?)?/gi, 'Mirally');
    if (document.title !== title) document.title = title;
  }
  install();
  const observer = new MutationObserver(records => {
    install();
    // Process only changed subtrees. Streamed message bodies never undergo
    // brand text replacement, and no whole-page scan runs on every token.
    const scopes = new Set();
    for (const record of records) {
      if (record.type === 'attributes' && record.target instanceof HTMLImageElement) scopes.add(record.target);
      if (record.type === 'characterData') {
        const parent = record.target.parentElement;
        if (parent?.matches(LABEL_SELECTOR)) scopes.add(parent);
      }
      for (const node of record.addedNodes) if (node.nodeType === Node.ELEMENT_NODE) scopes.add(node);
    }
    for (const scope of scopes) chrome(scope);
  });
  observer.observe(document, {childList:true, subtree:true, characterData:true, attributes:true, attributeFilter:['src']});
  chrome(document);
})();
