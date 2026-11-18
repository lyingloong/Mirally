export function hasNativeBrowserBridge(): boolean {
  const host:
    | (Window & {
        webkit?: { messageHandlers?: { mirallyBrowser?: { postMessage?: unknown } } };
      })
    | undefined = typeof window === "undefined" ? undefined : window;
  return typeof host?.webkit?.messageHandlers?.mirallyBrowser?.postMessage === "function";
}
