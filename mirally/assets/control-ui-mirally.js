/* A narrow, same-origin presentation layer. No RPC, storage or message routing. */
(() => {
  if (window.top !== window || location.origin !== __MIRALLY_ORIGIN__) return;
  if (window.__mirallyPresentation) return;
  window.__mirallyPresentation = {version: __MIRALLY_VERSION__};
  const MARK = __MIRALLY_MARK__;
  const CSS = __MIRALLY_STYLE__;
  const IMAGE_SELECTOR = '.topbar-brand__logo, .login-gate__logo, img.identity-avatar--agent, .chat-avatar.assistant, .identity-avatar__image';
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
  function chrome(scope) {
    images(scope);
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
  }
  install();
  const observer = new MutationObserver(records => {
    install();
    // Process only changed subtrees and image sources.
    const scopes = new Set();
    for (const record of records) {
      if (record.type === 'attributes' && record.target instanceof HTMLImageElement) scopes.add(record.target);
      for (const node of record.addedNodes) if (node.nodeType === Node.ELEMENT_NODE) scopes.add(node);
    }
    for (const scope of scopes) chrome(scope);
  });
  observer.observe(document, {childList:true, subtree:true, attributes:true, attributeFilter:['src']});
  chrome(document);
})();
