/** 全局状态。
 *
 *  界面一次展示一个账号，后台可以同时管理多个账号。一个 reactive 对象 + 几个
 *  refresh 函数就够了，看得见的地方越少越好。
 *
 *  `gate` 特意不缓存过期时间：闸门是会变的（消息来了、风控计数变了），
 *  顶栏要的是"现在能不能动"，不是"刚才能不能动"。
 */

import { reactive } from 'vue'
import { api, listen, req, type Gate, type Overview, type Quota, type PoolView } from './api'
import { desktop, play, unlock } from './sound'

export type Toast = { id: number; text: string; kind: 'ok' | 'warn' | 'danger' | 'info' }

export const state = reactive({
  ready: false,
  signedIn: false,
  overview: null as Overview | null,
  gate: null as Gate | null,
  quota: null as Quota | null,
  connected: false,
  guardError: '',
  overviewError: '',
  events: [] as { seq?:number; topic: string; at: number; text: string; account_id?:number; issue?:boolean; scope?:string }[],
  historyError: '',
  toasts: [] as Toast[],
  theme: (localStorage.getItem('tobili.theme') === 'dark' ? 'dark' : 'light') as 'dark' | 'light',
  /** 提示音开关。默认开 —— 这个工具存在的意义就是"别错过中奖"，安静才是反常。 */
  soundOn: localStorage.getItem('tobili.sound') !== '0',
  prizes: 0,
  unreadMessages: 0,
  queueCount: null as number | null,
  /** 最近一次看到的、值钱的那几条。收件箱页拿它做角标就够了。 */
  prizeKeys: [] as string[],
})

let seq = 0

export function toast(text: string, kind: Toast['kind'] = 'info') {
  const id = ++seq
  state.toasts.push({ id, text, kind })
  window.setTimeout(() => {
    const index = state.toasts.findIndex((item) => item.id === id)
    if (index >= 0) state.toasts.splice(index, 1)
  }, kind === 'danger' ? 9000 : 4200)
}

export function dismiss(id: number) {
  const index = state.toasts.findIndex((item) => item.id === id)
  if (index >= 0) state.toasts.splice(index, 1)
}

export function applyTheme(next: 'dark' | 'light') {
  state.theme = next
  localStorage.setItem('tobili.theme', next)
  document.documentElement.classList.toggle('light', next === 'light')
}

export function setSound(on: boolean) {
  state.soundOn = on
  localStorage.setItem('tobili.sound', on ? '1' : '0')
  if (on) {
    unlock()
    play('done')
  }
}

/** 提醒一次：响一声、弹个桌面通知、屏幕上飘一条。
 *
 *  三样一起来是刻意的。声音负责"人不在电脑前"，桌面通知负责"人切到别的窗口了"，
 *  toast 负责"人正看着这个页面" —— 单靠任何一个都会漏。
 */
export function alertPrize(title: string, body: string) {
  if (state.soundOn) play('prize')
  desktop(title, body)
  toast(body, 'warn')
}

let overviewRequest=0
let accountEpoch=0
export const currentAccount=()=>state.overview?.accounts.find(a=>a.current)?.id
let queueRequest=0
export function updateQueue(pool:PoolView, owner=currentAccount()) {
  if(owner!==currentAccount())return
  queueRequest++
  state.queueCount=(pool.counts.pending||0)+(pool.counts.running||0)
}
export async function refreshQueue() {
  const owner=currentAccount(), request=++queueRequest
  if(!state.signedIn||!owner){state.queueCount=null;return}
  try {
    const data=await req<{account_id:number;pending:number}>('/api/pool/count')
    if(request===queueRequest&&owner===currentAccount()&&data.account_id===owner)state.queueCount=data.pending
  } catch { /* Keep the last confirmed count during a temporary connection failure. */ }
}
export async function refreshOverview() {
  const request=++overviewRequest
  try {
    const body = await api.overview()
    if(request!==overviewRequest)return
    if(currentAccount()!==body.accounts.find(a=>a.current)?.id){
      accountEpoch++;queueRequest++;state.queueCount=null;state.gate=null;state.quota=null;state.prizes=0;state.unreadMessages=0;state.prizeKeys=[]
    }
    state.overview = body
    state.signedIn = body.signed_in
    state.overviewError = ''
  } catch (error) {
    // 一次请求失败不代表登录态失效。这里刻意**不动** signedIn：
    // 否则网络抖一下，界面就闪回扫码页，刚拿到的数据也跟着一起没了。
    state.overviewError = error instanceof Error ? error.message : String(error)
  } finally {
    state.ready = true
  }
}

/** 闸门与配额都依赖网络，失败不抛 —— 顶栏少一个角标比整页报错好。
 *  但失败原因要留下来：顶栏写着"未知"却不说为什么，等于没写。
 */
export async function refreshGuard() {
  if (!state.signedIn) {
    state.gate = null
    state.quota = null
    state.guardError = ''
    return
  }
  const epoch=accountEpoch
  const [gate, quota] = await Promise.allSettled([api.gate(), api.quota()])
  if(epoch!==accountEpoch)return
  state.gate = gate.status === 'fulfilled' ? gate.value : null
  state.quota = quota.status === 'fulfilled' ? quota.value : null
  const problems = [gate, quota]
    .filter((result) => result.status === 'rejected')
    .map((result) => String((result as PromiseRejectedResult).reason?.message ?? result.reason))
  state.guardError = problems.join(' / ')
}

export async function refreshAll() {
  await refreshOverview()
  await Promise.all([refreshGuard(),refreshQueue()])
}

const TOPIC_TEXT: Record<string, string> = {
  scan: '订阅',
  'source.update': '订阅',
  'result.discard': '移除',
  settle: '核验',
  llm: '模型',
  probe: '检查状态',
  write: '执行动作',
  'participation.issue': '参与待处理',
  'participation.step': '参与步骤',
  settled: '已提交',
  duplicate: '已有记录',
  'daemon.round_done': '定时轮完成',
  'daemon.round_failed': '定时轮失败',
  'account.switched': '切换账号',
  'account.removed': '移除账号',
  'notify.sent': '通知已发出',
  'schedule.round': '账号排期完成',
  'notify.failed': '通知没发出去',
  'exchange.review': '识别资料修订',
}

export function describeEvent(topic: string, payload: Record<string, unknown>): string {
  const rawAction = String(payload.action ?? '')
  const action = ({follow:'关注',share:'转发',comment:'评论',like:'点赞',favorite:'收藏',reserve:'预约',unfollow:'取关',delete:'删除转发',delete_dyn:'删除转发'} as Record<string,string>)[rawAction] ?? rawAction
  const dyn = String(payload.dyn_id ?? '')
  const who = String(payload.nickname ?? '')
  const title = String(payload.title ?? '')
  const account = state.overview?.accounts.find(a=>a.id===payload.account_id)
  const detail = String(payload.message || payload.error || payload.note || (payload.state==='unknown'?'无法确认是否已执行': '') || '')
  const tail = [account?.nickname,who!==account?.nickname?who:'', action, title, dyn&&!title.includes(dyn)?dyn:'', detail && detail!=='OK'&&!title.includes(detail)?detail:'', Array.isArray(payload.problems)?payload.problems.filter(p=>!title.includes(String(p))).join('；'):''].filter(Boolean).join(' · ')
  return `${TOPIC_TEXT[topic] ?? topic}${tail ? `：${tail}` : ''}`
}
export function isIssue(topic:string,payload:Record<string,unknown>){return payload.ok===false||payload.ambiguous===true||payload.state==='unknown'||['error','warning'].includes(String(payload.level))||topic.includes('failed')||(Array.isArray(payload.problems)&&payload.problems.length>0)}

let inboxTimer: number | undefined
const knownByAccount=new Map<number,string[]>()
const inboxPending=new Set<number>()

/** 盯着收件箱，出现新的"可能在说中奖"的消息就提醒一次。
 *
 *  为什么是轮询而不是事件流：消息是 B 站那边产生的，本地没有任何事件可推。
 *  60 秒一次是折中 —— 更密只是多打几个请求，中奖通知也不差这一分钟。
 *
 *  **只提醒"新的"**：同一条消息每轮都响一遍，人第一件事就是把声音关掉。
 */
export function startInboxWatch() {
  if (inboxTimer) return
  const tick = async () => {
    const owner=currentAccount(),epoch=accountEpoch
    if (!state.signedIn||!owner||inboxPending.has(owner)) return
    inboxPending.add(owner)
    try {
      const previous=knownByAccount.get(owner)
      const known=previous||[]
      // 和消息页用同一个分页（1/60）：服务端按这个键缓存两分钟，
      // 两边读的是同一份，不会一边读完另一边又开始读。
      const view = await api.inbox(1, 60)
      if(epoch!==accountEpoch||owner!==currentAccount())return
      const fresh = view.items.filter((item) => item.prize && item.unread && !known.includes(item.key))
      state.prizes = view.counts.prize
      state.unreadMessages = view.counts.unread
      state.prizeKeys = view.items.filter((item) => item.prize).map((item) => item.key)
      if (fresh.length && previous!==undefined) {
        // 第一次拉到的历史消息不响铃 —— 刚打开软件就被过去三个月的通知轰炸，
        // 是最容易让人关掉提醒的体验。
        alertPrize(
          '可能有中奖消息',
          fresh.length > 1 ? `${fresh.length} 条新消息提到中奖` : `${fresh[0].who}：${fresh[0].short}`,
        )
      }
      knownByAccount.set(owner,view.items.filter((item) => item.prize).map((item) => item.key))
    } catch {
      // 收件箱读不到不影响任何主流程，等待下一轮。
    } finally {inboxPending.delete(owner)}
  }
  void tick()
  inboxTimer = window.setInterval(() => void tick(), 60000)
}

export function stopInboxWatch() {
  if (inboxTimer) window.clearInterval(inboxTimer)
  inboxTimer = undefined
}

/** 收件箱页自己看过之后，别让后台再因为同一批消息响一次。 */
export function markPrizesKnown(keys: string[]) {
  const owner=currentAccount();if(owner)knownByAccount.set(owner,keys)
}

let stop: (() => void) | null = null

export async function refreshHistory() {
  try {
    const data=await req<{items:{seq:number;topic:string;at:string;payload:Record<string,unknown>}[]}>('/api/history?limit=120')
    const merged=new Map<number,typeof state.events[number]>()
    for(const row of data.items) merged.set(row.seq,{seq:row.seq,topic:row.topic,at:Date.parse(row.at)/1000,account_id:row.payload.account_id as number|undefined,scope:String(row.payload.scope||''),text:describeEvent(row.topic,row.payload),issue:isIssue(row.topic,row.payload)})
    for(const row of state.events) if(row.seq)merged.set(row.seq,row)
    state.events=[...merged.values()].sort((a,b)=>b.at-a.at||(b.seq||0)-(a.seq||0)).slice(0,120)
    state.historyError=''
  } catch(e) { state.historyError='历史记录读取失败' }
}

/** 连上事件流。重复调用不会重复连接。 */
export function startEvents() {
  if (stop) return stop
  stop = listen(
    (topic, payload) => {
      const seq=Number(payload.seq)
      if(!state.events.some(e=>e.seq===seq))state.events.unshift({ seq,topic, account_id: payload.account_id as number|undefined, scope:String(payload.scope||''), at: Number(payload.at ?? Date.now() / 1000), text: describeEvent(topic, payload),issue:isIssue(topic,payload) })
      if (state.events.length > 120) state.events.length = 120
      // 有写操作发生，闸门与配额的数就可能变了，顺手刷一下。
      if (topic === 'settled' || topic === 'duplicate' || topic.startsWith('daemon.')) {
        void refreshGuard()
        void refreshQueue()
      }
    },
    (connected) => {
      state.connected = connected
      if(connected)void refreshHistory()
    },
  )
  return stop
}
