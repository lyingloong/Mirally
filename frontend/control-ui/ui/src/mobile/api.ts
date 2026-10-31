/** Phone credentials belong only to the restricted phone origin, never the desktop Host. */
export const MOBILE_TOKEN_KEY = 'mirally.mobile.token.v1';
export type Rpc = <T = any>(method: string, params?: Record<string, unknown>) => Promise<T>;
export class MobileApiError extends Error {
  constructor(message: string, readonly status = 0) { super(message); }
}
export function readMobileToken(): string {
  try { return sessionStorage.getItem(MOBILE_TOKEN_KEY) || ''; } catch { return ''; }
}
export function storeMobileToken(token: string) {
  try { if (token) sessionStorage.setItem(MOBILE_TOKEN_KEY, token); else sessionStorage.removeItem(MOBILE_TOKEN_KEY); } catch { /* Memory session remains usable. */ }
}
export function pairingCodeFromHash(hash: string): string {
  return new URLSearchParams(hash.replace(/^#/, '')).get('pair') || '';
}
async function post(path: string, body: unknown, token = '') {
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch(path, {method: 'POST', credentials: 'omit', cache: 'no-store', redirect: 'error',
      headers: {'Content-Type': 'application/json', ...(token ? {Authorization: `Bearer ${token}`} : {})},
      body: JSON.stringify(body), signal: controller.signal});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.error) {
      const detail = typeof payload.error === 'string' ? payload.error : payload.error?.message;
      throw new MobileApiError(typeof detail === 'string' ? detail.slice(0, 1000) : response.status === 401 ? '连接已失效，请重新配对。' : '电脑暂时无法完成操作，请刷新状态。', response.status);
    }
    return payload.result ?? payload;
  } catch (error) {
    if (error instanceof MobileApiError) throw error;
    throw new MobileApiError('暂时无法连接电脑。请检查手机的私人网络与电脑上的 Mirally；未确认的操作不会显示为成功。');
  } finally { clearTimeout(timeout); }
}
export const pairMobile = (code: string, device_name: string) => post('/api/mobile/pair', {code, device_name});
export function createMobileRpc(token: string): Rpc {
  return (method, params = {}) => post('/api/mobile/rpc', {method, params}, token);
}
export type MobilePreferences = {
  home: 'work' | 'conversations' | 'last'; navigation: string[]; hidden: string[];
  work_sort: 'attention' | 'updated'; show_completed: boolean; conversation_sort: 'updated' | 'pinned';
};
export const DEFAULT_MOBILE_PREFERENCES: MobilePreferences = {
  home: 'conversations', navigation: ['work', 'conversations', 'inbox', 'settings'], hidden: [], work_sort: 'attention',
  show_completed: true, conversation_sort: 'updated',
};
export function mobileNavigation(preferences: MobilePreferences) {
  const known = ['conversations', 'inbox', 'settings'];
  const ordered = [...new Set([...preferences.navigation.filter(id => known.includes(id)), ...known])];
  return ordered.filter(id => id === 'settings' || !preferences.hidden.includes(id));
}
export function timeLabel(value: unknown) {
  if (value === undefined || value === null || value === '') return '时间未记录';
  const millis = typeof value === 'number' ? value : Date.parse(String(value));
  return Number.isFinite(millis) ? new Date(millis).toLocaleString() : '时间未记录';
}
