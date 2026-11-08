/** Protect unfinished page work before account changes, including QR login. */
const guards=new Set<()=>boolean|Promise<boolean>>()
export function registerWorkGuard(next: () => boolean | Promise<boolean>) {
  guards.add(next)
  return ()=>guards.delete(next)
}
export async function canChangeAccount(){for(const guard of guards){if(!await guard())return false}return true}
