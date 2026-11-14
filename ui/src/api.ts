/** 与后端的唯一通道。
 *
 *  所有路径都写相对路径：开发时 Vite 代理转发，打包后同源直连，代码里不必区分。
 *  错误统一成 ApiError 并保留后端的中文 detail —— 后端的报错文案本身就是给人看的，
 *  前端再包一层"请求失败"只会把关键信息藏起来。
 */

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController()
  const reading = !init.method || init.method === 'GET'
  const abort = () => controller.abort()
  init.signal?.addEventListener('abort', abort, { once: true })
  if (init.signal?.aborted) controller.abort()
  const timer = reading ? setTimeout(abort, 20000) : undefined
  try {
  const response = await fetch(path, {
    ...init,
    signal: controller.signal,
    headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
  })
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const body = await response.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* 后端没给 JSON，用状态码兜底 */
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
  } catch (error) {
    if (controller.signal.aborted && !init.signal?.aborted)
      throw new ApiError(408, '读取超时，请重试；后台任务未被停止')
    throw error
  } finally {
    if (timer) clearTimeout(timer)
    init.signal?.removeEventListener('abort', abort)
  }
}

export const post = <T>(path: string, body?: unknown) =>
  req<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

// ---- 类型 ----
export interface Profile {
  uid: string
  nickname: string
  level: number
  avatar: string | null
}

export interface Overview {
  signed_in: boolean
  account: Profile | null
  /** 所有可切换的账号。`ready` 是"凭据还在不在"——有账号行不等于能干活。 */
  accounts: AccountRow[]
  encrypted: boolean
  campaigns: { total: number; open: number }
  enrollments: { total: number; settled: number }
  unresolved_actions: number
}

export interface AccountRow extends Profile {
  id: number
  current: boolean
  ready: boolean
  session?: AccountSession
}

export interface AccountSession {
  state: 'unknown' | 'checking' | 'valid' | 'unavailable' | 'login_required'
  note: string
  checked_at: number
  checking?: boolean
}

export interface Gate {
  open: boolean
  reason: string
  hint: string
  paused: boolean
  risk_hits: number
  unread_chat: number
  unread_system: number
  acked_chat: number
  acked_system: number
  readable: boolean
}

export interface Quota {
  verdict: string
  allowed: boolean
  explain: string
  wait_seconds: number
  last_hour: number
  last_day: number
  budget: { per_hour: number; per_day: number; min_gap_seconds: number }
}

export interface Feed {
  id: number
  kind: string
  target: string
  label: string
  enabled: boolean
  cursor: string
  last_scan_at: string | null
  error?: string
  attempted_at?: string | null
}

export interface PlanLine {
  enrollment_id: number
  dyn_id: string
  up_name: string
  action: string
  allowed: boolean
  reason: string
  decided_at: string | null
}

export interface Preview {
  plan_id: number
  persisted: boolean
  clearable: number
  items: PlanLine[]
}

export interface PlanItem {
  reward: string
  id: number
  campaign_id: number
  action: string
  target: string
  snapshot: string
  state: string
  reason: string
  executed_at: string | null
  dyn_id: string
  up_uid: string
  title: string
  up_name: string
  decided_at: string | null
}

export interface Plan {
  plan_id: number
  state: string
  note: string
  created_at: string
  confirmed_at: string | null
  counts: Record<string, number>
  items: PlanItem[]
}

export interface PlanSummary {
  id: number
  state: string
  note: string
  created_at: string
  previewed_at: string | null
  confirmed_at: string | null
  finished_at: string | null
  total: number
  pending: number
  done: number
}

export interface ExecItem {
  item_id: number
  dyn_id: string
  up_name: string
  action: string
  state: string
  message: string
}

export interface ExecReport {
  plan_id: number
  state: string
  applied: boolean
  done: number
  failed: number
  skipped: number
  planned: number
  note: string
  /** 执行前的库快照路径。空表示这次没真执行（预演不备份）。 */
  snapshot: string
  items: ExecItem[]
}

export interface ScanReport {
  reused?: number
  feeds: number
  scanned: number
  matched: number
  unique: number
  duplicates: number
  /** 这轮里能对上官方抽奖索引的条数 */
  official?: number
  /** 只能靠正文推断、没有官方依据的条数 */
  guessed?: number
  sources: Record<string, number>
  problems: string[]
}

export interface Candidate {
  uid: string
  name: string
  fans: number
  sign: string
  score: number
  reasons: string[]
}

export interface DiscoverReport {
  keyword: string
  candidates: Candidate[]
  added: number[]
  problems: string[]
}

export interface SettleItem {
  enrollment_id: number
  dyn_id: string
  up_name: string
  outcome: string
  evidence: string
}

export interface SettleReport {
  checked: number
  decided: number
  applied: boolean
  items: SettleItem[]
  problems: string[]
}

export interface QrTicket {
  key: string
  url: string
}

export interface PoolItem {
  up_uid: string
  reward?: string
  campaign_id: number
  dyn_id: string
  title: string
  up_name: string
  kind: string
  deadline: string | null
  position: number
  state: string
  message: string
}

export interface PoolView {
  progress?: string
  items: PoolItem[]
  paused: boolean
  note: string
  counts: Record<string, number>
  limit: number
}

export type PoolOp = 'add' | 'remove' | 'move' | 'retry' | 'clear_done' | 'pause' | 'resume'

export interface PoolEdit {
  operation: PoolOp
  campaign_ids?: number[]
  campaign_id?: number
  direction?: number
}

export interface PoolRunLine {
  campaign_id: number
  dyn_id: string
  title: string
  state: string
  message: string
}

export interface PoolRunReport {
  applied: boolean
  attempted: number
  joined: number
  skipped: number
  failed: number
  review: number
  pending: number
  paused: boolean
  note: string
  lines: PoolRunLine[]
}

/** 活动库里的一行：活动本身 + 我的报名 + 开奖结论 + 清理状态。 */
export interface CampaignRow {
  draw_state?: string
  draw_reason?: string
  draw_business_type?: number
  deadline_date?: string | null
  expires_at?: string | null
  shelf_reason: string|null
  published_at: string|null
  reward_status?: string
  queued: boolean
  id: number
  dyn_id: string
  up_uid: string
  up_name: string
  title: string
  reward: string
  kind: string
  deadline_at: string | null
  confidence: number
  enrollment_id: number | null
  enrollment_stage: string | null
  verdict: string | null
  verdict_source: string
  verdict_note: string
  decided_at: string | null
  cleaned: number
  kept: number
}

// ---- 收件箱 ----
export interface InboxItem {
  review_key?: string
  reviewed?: boolean
  automatic?: boolean
  sender_uid?: string
  kind: 'chat' | 'reply' | 'at' | string
  key: string
  who: string
  text: string
  /** 被回复的是哪条（标题/摘要）。只做显示，不参与中奖判定。 */
  context: string
  short: string
  at: string | null
  unread: boolean
  link: string
  prize: boolean
  reason: string
}

export interface InboxView {
  items: InboxItem[]
  counts: {
    total: number
    unread: number
    prize: number
    chat: number
    system: number
    reply: number
    at: number
  }
  /** 系统通知中心的未读条数。那里没有列表接口，只有数字；能读到的系统卡片在 items 里（kind=system）。 */
  system_count: number
  problems: string[]
  note: string
}

// ---- 参与排期 ----
export interface ScheduleRun {
  at: string
  ok: boolean
  note: string
  scanned: number
  written: number
}

export interface ScheduleState {
  account_id: number
  selection: "queue" | "catalog"
  running: boolean
  interval_min: number
  apply: boolean
  next_at: string | null
  last: ScheduleRun | null
  runs: ScheduleRun[]
}

// ---- 设置 ----
export interface LlmState {
  health?: {ok?: boolean; error?: string; at?: number; retry_at?: number}
  scan_enabled: boolean
  enabled: boolean
  base_url: string
  model: string
  key_set: boolean
  ready: boolean
}

export interface Settings {
  mode: string
  custom: string
  fallback: string
  llm: LlmState
}

export interface NotifyState {
  enabled: boolean
  /** 已遮过的地址，只够认出"配过没有"。 */
  webhook: string
  has_webhook: boolean
  has_secret: boolean
}

// ---- 调用 ----
export const api = {
  overview: () => req<Overview>('/api/overview'),

  qr: () => post<QrTicket>('/api/auth/qr'),
  pollQr: (key: string) =>
    req<{ state: string; account?: Profile; encrypted?: boolean }>(
      `/api/auth/qr/${encodeURIComponent(key)}`,
    ),
  signOut: () =>
    req<{ state: string; current: number | null }>('/api/auth/session', { method: 'DELETE' }),

  accounts: () => req<{ items: AccountRow[]; current: number | null }>('/api/accounts'),
  switchAccount: (accountId: number) =>
    post<{ account: Profile }>('/api/accounts/switch', { account_id: accountId }),
  removeAccount: (accountId: number) =>
    req<{ state: string; current: number | null }>(`/api/accounts/${accountId}`, {
      method: 'DELETE',
    }),

  feeds: () => req<{ items: Feed[] }>('/api/feeds'),
  addFeed: (body: { kind: string; target: string; label: string }) => post<{ id: number }>('/api/feeds', body),
  /** 真删。停用请用 patchFeed({ enabled: false })。 */
  deleteFeed: (id: number) => req<{ state: string }>(`/api/feeds/${id}`, { method: 'DELETE' }),
  patchFeed: (id: number, body: { enabled?: boolean; label?: string }) =>
    req<{ state: string }>(`/api/feeds/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  scan: (pages = 1, limit = 20, history = false, feedId?: number, feedIds?: number[]) =>
    post<ScanReport>(`/api/feeds/scan?pages=${pages}&limit=${limit}&history=${history}${feedId ? `&feed_id=${feedId}` : ''}${feedIds?.map(id=>`&feed_ids=${id}`).join('')||''}`),
  discover: (keyword: string, limit = 10, apply = false) =>
    post<DiscoverReport>('/api/feeds/discover', { keyword, limit, apply }),

  inbox: (pages = 1, limit = 60, refresh = false) =>
    req<InboxView>(`/api/inbox?pages=${pages}&limit=${limit}${refresh ? '&refresh=1' : ''}`),
  markSeen: (keys: string[], account_id?: number) => post<{ marked: number }>('/api/inbox/seen', { keys, account_id }),
  clearSeen: () => req<{ state: string }>('/api/inbox/seen', { method: 'DELETE' }),

  settings: () => req<Settings>('/api/settings'),

  // ---- 参与排期 ----
  schedule: () => req<ScheduleState>('/api/schedule'),
  startSchedule: (body: { interval_min: number; account_id: number; selection: string; apply: boolean; confirmed: boolean }) =>
    req<ScheduleState>('/api/schedule', { method: 'POST', body: JSON.stringify(body) }),
  stopSchedule: (id: number) => req<ScheduleState>(`/api/schedule?account_id=${id}`, { method: 'DELETE' }),
  /** 立刻跑一轮，不改排期。 */
  runScheduleOnce: (account_id: number, selection: string, apply: boolean) =>
    post<ScheduleRun>('/api/schedule/once', { account_id, selection, apply, confirmed: apply }),
  shelve: () => post<{count:number}>('/api/campaigns/shelf'),
  dismiss: (id:number,prizeOnly=false) => post(`/api/campaigns/shelf/${id}?prize_only=${prizeOnly}`),
  restore: (id:number) => req(`/api/campaigns/shelf/${id}`, {method:'DELETE'}),
  review: (id:number, outcome:string, note:string, account_id:number, protect:boolean) =>
    req(`/api/results/${id}`, {method:'PUT',body:JSON.stringify({outcome,note,account_id,protect})}),
  notifyState: () => req<NotifyState>('/api/settings/notify'),
  saveNotify: (body: { webhook?: string; secret?: string; enabled: boolean }) =>
    req<NotifyState>('/api/settings/notify', { method: 'PUT', body: JSON.stringify(body) }),
  clearNotify: () => req<NotifyState>('/api/settings/notify', { method: 'DELETE' }),
  testNotify: () => post<{ ok: boolean; detail: string }>('/api/settings/notify/test'),
  saveLlm: (body: { scan_enabled?: boolean; base_url?: string; model?: string; api_key?: string; enabled?: boolean }) =>
    req<Settings>('/api/settings/llm', { method: 'PUT', body: JSON.stringify(body) }),
  testLlm: () => post<{ ok: boolean; reply: string; model: Record<string, unknown> }>('/api/settings/llm/test'),
  saveText: (body: { mode?: string; custom?: string; fallback?: string }) =>
    req<Settings>('/api/settings/text', { method: 'PUT', body: JSON.stringify(body) }),
  /** 预览一条转发语。不发送、不写库 —— 用来在放开自动参与之前先看几条。 */
  previewText: (body: { up_name?: string; title?: string; body?: string; dyn_id?: string }) =>
    post<{ text: string; source: string; note: string; fallback: boolean }>(
      '/api/settings/text/preview',
      body,
    ),

  quota: () => req<Quota>('/api/quota'),
  settle: (apply = false, limit = 20) =>
    post<SettleReport>(`/api/settle?apply=${apply}&limit=${limit}`),


  gate: () => req<Gate>('/api/tidy/gate'),
  pause: (reason: string) => post<{ paused: boolean; reason: string }>('/api/tidy/pause', { reason }),
  resume: () => post<{ open: boolean; reason: string; hint: string }>('/api/tidy/resume'),
  ack: () => post<{ acked_chat: number; acked_system: number }>('/api/tidy/ack'),

  preview: (limit?: number) =>
    post<Preview>(`/api/tidy/plans${limit ? `?limit=${limit}` : ''}`),
  plans: (limit = 10) => req<{ items: PlanSummary[] }>(`/api/tidy/plans?limit=${limit}`),
  plan: (id: number) => req<Plan>(`/api/tidy/plans/${id}`),
  select: (id: number, itemIds: number[]) =>
    post<{ plan_id: number; selected: number; dropped: number; revived: number }>(
      `/api/tidy/plans/${id}/items/select`,
      { item_ids: itemIds },
    ),
  confirm: (id: number) =>
    post<{ plan_id: number; state: string }>(`/api/tidy/plans/${id}/confirm`),
  execute: (id: number, apply: boolean, limit?: number) =>
    post<ExecReport>(`/api/tidy/plans/${id}/run`, { apply, limit: limit ?? null }),
  undo: (itemId: number) => post<{ item_id: number; ok: boolean; message: string }>(
    `/api/tidy/items/${itemId}/undo`,
  ),

  pool: () => req<PoolView>('/api/pool'),
  editPool: (body: PoolEdit) => post<PoolView>('/api/pool', body),
  runPool: (limit = 0) =>
    post<PoolRunReport>('/api/pool/run', { apply: true, limit }),

  campaigns: (params: { keyword?: string; kind?: string; state?: string; statuses?:string; prize_state?:string; availability?:string; official?:string; time_field?:string; after?:string; before?:string; order?:string; limit?: number; offset?: number } = {}) => {
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== '') query.set(key, String(value))
    }
    const suffix = query.toString()
    return req<{ items: CampaignRow[]; total: number; counts: Record<string, number> }>(
      `/api/campaigns${suffix ? `?${suffix}` : ''}`,
    )
  },
}

/** 事件流。返回一个关停函数，组件卸载时必须调用，否则重连循环会一直活着。 */
export function listen(
  onEvent: (topic: string, payload: Record<string, unknown>) => void,
  onState: (connected: boolean) => void,
): () => void {
  let source: EventSource | null = null
  let closed = false
  let retry = 0
  let timer: number | undefined

  const connect = () => {
    if (closed) return
    source = new EventSource('/api/events')
    source.onopen = () => {
      retry = 0
      onState(true)
    }
    source.onmessage = (event) => {
      try {
        const body = JSON.parse(event.data) as Record<string, unknown>
        onEvent(String(body.topic ?? ''), {...body,seq:Number(event.lastEventId)})
      } catch {
        /* 心跳和注释行不是 JSON，忽略 */
      }
    }
    source.onerror = () => {
      onState(false)
      source?.close()
      source = null
      if (closed) return
      // 后端重启时浏览器会自动重连，但那会把退避策略写死在浏览器里。
      // 自己控制指数退避，最长 15 秒，避免后端关着时刷屏。
      retry += 1
      timer = window.setTimeout(connect, Math.min(15000, 800 * 2 ** retry))
    }
  }

  connect()
  return () => {
    closed = true
    if (timer) window.clearTimeout(timer)
    source?.close()
  }
}
