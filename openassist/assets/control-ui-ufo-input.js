(() => {
  if (location.origin !== __MIRALLY_ORIGIN__ || window.__mirallyUfoInput) return;
  window.__mirallyUfoInput = true;
  let previous;
  const update = () => {
    let field = document.activeElement;
    while (field?.shadowRoot?.activeElement) field = field.shadowRoot.activeElement;
    const editable = field?.matches?.("textarea, input:not([type=button]):not([type=submit]), [contenteditable=true]");
    // Sending clears the native composer; an empty focused composer must not
    // block the desktop task it just submitted.
    const editing = Boolean(document.hasFocus() && editable &&
      String(field.value ?? field.textContent ?? "").length);
    if (editing === previous) return;
    previous = editing;
    window.chrome?.webview?.postMessage({type: "mirally-ufo-input", editing});
  };
  for (const event of ["focusin", "focusout", "input", "click"]) document.addEventListener(event, update, true);
  window.addEventListener("blur", update);
  window.addEventListener("focus", update);
  setInterval(update, 150);
  update();
})();
