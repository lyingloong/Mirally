/**
 * Product pages use the same authenticated Mirally transport as chat and the
 * retained native presentation. This module never owns a connection or token.
 * RPC results are unwrapped by the host; request() returns a full HTTP body.
 */
export type ProductData = Record<string, unknown>;

export interface ProductHost {
  request<T = unknown>(path: string, options?: RequestInit): Promise<T>;
  rpc<T = unknown>(method: string, params?: ProductData): Promise<T>;
  client: {
    readonly connected: boolean;
    request<T = unknown>(method: string, params?: Record<string, unknown>): Promise<T>;
    addEventListener?: (listener: (event: {event: string; payload?: unknown}) => void) => () => void;
  };
  conversationIdForSession?: (sessionKey: string) => string;
  getUiPreference?: (key: string) => unknown;
  setUiPreference?: (key: string, value: string) => void;
  openBrowser?: (url: string) => Promise<unknown>;
  confirmCapture?: (scope: unknown) => Promise<boolean>;
  notify?: (message: string) => void;
}

type ProductWindow = Window & {
  __mirallyHost?: ProductHost;
  __mirallyFramework?: {showPage(page: string): Promise<unknown>};
};

export function getProductHost(): ProductHost {
  const host = (window as ProductWindow).__mirallyHost;
  if (!host?.request || !host?.rpc) {
    throw new Error("Mirally 产品连接尚未就绪，请稍后重试。");
  }
  return host;
}

/** Preserve the host's same-origin checks, authentication and reconnect path. */
export function requestProduct<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  return getProductHost().request<T>(path, options);
}

/** Use only public product methods. The server remains the capability owner. */
export function rpcProduct<T = unknown>(method: string, params: ProductData = {}): Promise<T> {
  return getProductHost().rpc<T>(method, params);
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Opens an existing Mirally surface, including its original native overlay. */
export async function openProductPage(page: string): Promise<void> {
  const framework = (window as ProductWindow).__mirallyFramework;
  if (!framework?.showPage) throw new Error("Mirally 页面尚未就绪，请稍后重试。");
  await framework.showPage(page);
}
