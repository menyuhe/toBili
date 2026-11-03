/** View identity only. The server never uses this as a process-wide account. */
export let activeUid = globalThis.sessionStorage?.getItem("tobili.account") || "none";
export let accountGeneration = 0;
export const STALE_ACCOUNT_MESSAGE = '账号已切换，已忽略旧请求';
export function selectIdentity(uid: string) {
  activeUid = uid || "none";
  accountGeneration++;
  globalThis.sessionStorage?.setItem("tobili.account", activeUid);
}
export function accountUrl(url: string) {
  return `${url}${url.includes("?") ? "&" : "?"}account_uid=${encodeURIComponent(activeUid)}`;
}
