(() => {
  'use strict';
  if (window.top !== window || location.origin !== __MIRALLY_ORIGIN__ || !window.chrome?.webview) return;
  const pending = new Map();
  const current = () => window.__mirallyIntake?.active();

  function receive(result) {
    const request = pending.get(result?.requestId);
    if (!request) return;
    pending.delete(result.requestId);
    clearTimeout(request.timer);
    const pane = current();
    if (pane !== request.pane || pane?.state !== request.state || pane.state.sessionKey !== request.key) {
      request.reject(new Error('会话已经切换，这次材料没有添加，请重新拖动。'));
    } else if (result.error) request.reject(new Error(result.error));
    else if (typeof result.dataUrl !== 'string' || result.dataUrl.length > 20_000_000 ||
      !/^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(result.dataUrl)) {
      request.reject(new Error('未收到有效的窗口画面。'));
    } else {
      try {
        const bytes = Uint8Array.from(atob(result.dataUrl.split(',')[1]), c => c.charCodeAt(0));
        const name=String(result.title || '窗口材料').replace(/[<>:"/\\|?*\x00-\x1f]/g,'').slice(0,70) || '窗口材料';
        request.resolve({...result, file: new File([bytes], name+'.png', {type: 'image/png'})});
      } catch { request.reject(new Error('窗口画面无法读取，请重试。')); }
    }
  }

  function capture(pane) {
    if (!pane?.state || !window.chrome?.webview) return Promise.reject(new Error('请先打开一个会话，再拖向目标窗口。'));
    const requestId = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(requestId);
        reject(new Error('读取窗口超时，请重试或添加文件。'));
      }, 20000);
      pending.set(requestId, {pane, state: pane.state, key: pane.state.sessionKey, resolve, reject, timer});
      window.chrome.webview.postMessage({type: 'mirally-companion', action: 'capture-target', requestId});
    });
  }

  function bind(handle, options = {}) {
    let drag, busy = false;
    const down = event => {
      if (busy || !event.isPrimary || event.button !== 0 || event.altKey) return;
      const pane = current();
      drag = {id: event.pointerId, x: event.clientX, y: event.clientY, pane,
        state: pane?.state, key: pane?.state?.sessionKey, moved: false};
      handle.setPointerCapture(event.pointerId);
      event.preventDefault();
    };
    const move = event => {
      if (drag?.id !== event.pointerId) return;
      if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) >= 8) {
        drag.moved = true;
        handle.dataset.dragTarget = 'true';
      }
    };
    const cancel = () => { drag = null; delete handle.dataset.dragTarget; };
    const up = async event => {
      if (drag?.id !== event.pointerId) return;
      const gesture = drag;
      cancel();
      if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
      if (!gesture.moved) return;
      busy = true;
      handle.dataset.capturing = 'true';
      try {
        if (current() !== gesture.pane || gesture.pane?.state !== gesture.state ||
          gesture.state?.sessionKey !== gesture.key) throw new Error('会话已经切换，请重新拖动。');
        options.onStart?.();
        const result = await capture(gesture.pane);
        if (options.onReady) await options.onReady(result);
        else {
          if (!await window.__mirallyIntake.attach([result.file])) throw new Error('材料暂时无法添加，请重试。');
          window.__mirallyCompanion?.expand?.(true);
        }
      } catch (error) { options.onError?.(error.message); }
      finally { busy = false; delete handle.dataset.capturing; }
    };
    handle.addEventListener('pointerdown', down);
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
    handle.addEventListener('pointercancel', cancel);
    return () => {
      cancel();
      handle.removeEventListener('pointerdown', down);
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      handle.removeEventListener('pointercancel', cancel);
    };
  }

  window.__mirallyContext = {bind, receive};
  window.addEventListener('pagehide',()=>{
    for(const request of pending.values()){clearTimeout(request.timer);request.reject(new Error('页面已关闭，材料没有添加。'));}
    pending.clear();
  },{once:true});
})();
