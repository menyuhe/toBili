<script setup lang="ts">
import AccountBrowser from '../components/AccountBrowser.vue'
import ReviewEntry from "../components/ReviewEntry.vue"
/** 收件箱。
 *
 *  这一页的排序规则反直觉：**中奖相关的排在最前**，而不是最新的在最前。
 *  按时间排的话，几条"帮我投票"就能把真正的开奖通知挤到第二屏 —— 而这个工具
 *  存在的全部理由就是别错过那几条。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { api, type InboxItem, type InboxView } from '../api'
import { markPrizesKnown, state, toast } from '../store'

let alive=true
onUnmounted(()=>{alive=false})
const view = ref<InboxView | null>(null)
const loading = ref(true)
const busy = ref('')
const owner=state.overview?.accounts.find(a=>a.current)?.id
const filter = ref<'all'|'unread'|'prize'>('all')

const items = computed<InboxItem[]>(() => {
  const rows = view.value?.items ?? []
  const sorted = [...rows].sort((a, b) => {
    if (a.prize !== b.prize) return a.prize ? -1 : 1
    return (b.at ?? '').localeCompare(a.at ?? '')
  })
  return sorted.filter(row => filter.value==='all'||(filter.value==='unread'?row.unread:row.prize))
})

const unread = computed(() => (view.value?.items??[]).filter((row) => row.unread))

function kindLabel(kind:string){return ({ chat:'私信', system:'系统', reply:'回复', at:'@我' } as Record<string,string>)[kind]??kind}

function when(at: string | null) {
  if (!at) return ''
  const date = new Date(at)
  const diff = Date.now() - date.getTime()
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

async function load() {
  loading.value = true
  const owner=state.overview?.accounts.find(a=>a.current)?.id
  try {
    const result = await api.inbox(1, 60)
    if(!alive||owner!==state.overview?.accounts.find(a=>a.current)?.id)return
    view.value = result
    state.prizes = view.value.counts.prize
    // 这一页已经摆在人眼前了，后台就别再为同一批消息响一次。
    markPrizesKnown(view.value.items.filter((row) => row.prize).map((row) => row.key))
  } catch (error) {
    toast(error instanceof Error ? error.message : '读取消息失败', 'danger')
  } finally {
    loading.value = false
  }
}

async function seen(keys: string[]) {
  if (!keys.length) return
  busy.value = 'seen'
  try {
    await api.markSeen(keys,owner)
    await load()
  } catch (error) {
    toast(error instanceof Error ? error.message : '标记失败', 'danger')
  } finally {
    busy.value = ''
  }
}

async function clearSeen() {
  const ok = window.confirm(
    '清掉本机的「已看」记录？\n\n消息本身不会消失，B 站的红点也不会变。\n清掉之后这些消息会重新算作未读。',
  )
  if (!ok) return
  try {
    await api.clearSeen()
    toast('已重置，消息重新算未读', 'ok')
    await load()
  } catch (error) {
    toast(error instanceof Error ? error.message : '重置失败', 'danger')
  }
}

onMounted(load)
</script>

<template>
  <div class="max-w-[1080px] space-y-4">
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <button class="panel inbox-summary" :class="{'summary-selected':filter==='unread'}" :aria-pressed="filter==='unread'" @click="filter=filter==='unread'?'all':'unread'">
        <div class="text-[11.5px] muted">列表未看 · 点击筛选</div>
        <div class="text-[20px] font-semibold mt-1 tabular-nums">
          {{ unread.length }}
        </div>
        <div class="text-[11.5px] muted mt-1">
          私信 {{ view?.counts.chat ?? 0 }} · 系统 {{ view?.counts.system ?? 0 }} · 回复 {{ view?.counts.reply ?? 0 }} · @我 {{ view?.counts.at ?? 0 }}
        </div>
      </button>

      <section class="panel px-4 py-3" :style="(view?.counts.prize ?? 0) > 0 ? { borderColor: 'var(--warn)' } : {}">
        <div class="text-[11.5px] muted">可能中奖</div>
        <div class="text-[20px] font-semibold mt-1 tabular-nums" :style="(view?.counts.prize ?? 0) > 0 ? { color: 'var(--warn)' } : {}">
          {{ view?.counts.prize ?? 0 }}
        </div>
        <div class="text-[11.5px] muted mt-1">提到中奖、兑奖、收货地址的</div>
      </section>

      <section class="panel px-4 py-3">
        <div class="text-[11.5px] muted">B 站通知中心未读</div>
        <div class="text-[20px] font-semibold mt-1 tabular-nums">{{ view?.system_count ?? 0 }}</div>
        <div class="text-[11.5px] muted mt-1">
          <AccountBrowser url="https://message.bilibili.com/#/system" label="查看通知" />
        </div>
      </section>

      <section class="panel px-4 py-3 flex flex-col justify-between">
        <div class="text-[11.5px] muted">操作</div>
        <div class="flex flex-wrap gap-1.5 mt-2">
          <button class="mini" :disabled="!unread.length || busy === 'seen'" @click="seen(unread.map((row) => row.key))">
            列表全标已看
          </button>
          <button class="mini" @click="load">刷新</button>
          <button class="mini" @click="clearSeen">重置已看</button>
        </div>
      </section>
    </div>

    <section class="panel">
      <div class="panel-head">
        <div>
          <div class="text-[14.5px] font-semibold">消息</div>
          <div class="text-[12px] muted mt-0.5">
            {{filter==='unread'?'未看消息':filter==='prize'?'可能中奖':'全部消息'}} · {{ items.length }} 条
          </div>
        </div>
        <div class="message-filters" aria-label="消息筛选">
          <button class="mini" :class="{on:filter==='all'}" :aria-pressed="filter==='all'" @click="filter='all'">全部</button>
          <button class="mini" :class="{on:filter==='unread'}" :aria-pressed="filter==='unread'" @click="filter='unread'">未看 {{unread.length}}</button>
          <button class="mini" :class="{on:filter==='prize'}" :aria-pressed="filter==='prize'" @click="filter='prize'">可能中奖</button>
        </div>
      </div>

      <div v-if="loading" class="p-8 text-center text-[13.5px] muted">读取中…</div>

      <div v-else-if="!items.length" class="px-6 py-14 text-center">
        <div class="text-[14px] font-medium">{{filter==='unread'?'列表中的消息已全部看过':filter==='prize'?'没有可能中奖的消息':'暂无消息'}}</div>
      </div>

      <div v-else class="message-list">
        <div
          v-for="row in items"
          :key="row.key"
          class="msg"
          :class="{ 'msg-prize': row.prize, 'msg-unread': row.unread }"
        >
          <div class="message-content">
          <div class="message-heading">
            <span class="chip" :class="row.prize ? 'chip-warn' : ''">{{ kindLabel(row.kind) }}</span>
            <span class="text-[13.5px] font-medium truncate">{{ row.who }}</span>
            <span v-if="row.reviewed" class="chip">与抽奖无关</span><span v-if="row.automatic" class="chip auto-reply">自动回复</span>
            <span v-if="row.who==='名称待获取'&&row.sender_uid" class="muted text-xs">UID {{row.sender_uid}}</span>
            <span v-if="row.prize" class="badge-prize">{{ row.reason }}</span>
            <button v-if="row.unread" class="unread-tag" :disabled="busy==='seen'" @click="seen([row.key])" title="标记这条消息已看"><span class="dot"/>未看</button>
          </div>
          <div class="text-[13px] mt-1.5 leading-relaxed" :class="row.unread ? '' : 'muted'">
            {{ row.text || '（没有文字内容）' }}
          </div>
          <div v-if="row.context" class="text-[11.5px] muted mt-1 truncate">
            {{ row.kind === 'reply' ? '回复的是' : '主题' }}「{{ row.context }}」
          </div>
          </div>
          <div class="message-side">
            <time class="muted" :datetime="row.at||undefined">{{ when(row.at) }}</time>
            <div class="message-actions">
            <ReviewEntry :message="row.key" :review-key="row.review_key" :reviewed="row.reviewed" @saved="seen([row.key])" />
            <AccountBrowser v-if="row.link" :url="row.link" />
            </div>
            <span v-if="!row.unread" class="muted">本机已看</span>
          </div>
        </div>
      </div>

      <div v-if="view?.problems.length" class="border-t px-4 py-2.5" style="border-color: var(--line-soft)">
        
        <p v-for="problem in view.problems" :key="problem" class="text-[11.5px] mt-1" style="color: var(--warn)">
          {{ problem }}
        </p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.inbox-summary{text-align:left;padding:12px 16px;white-space:normal;background:var(--surface);display:block;font-weight:400}.inbox-summary.summary-selected{border-color:var(--discover);box-shadow:inset 0 0 0 1px var(--discover)}
.message-filters{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.message-list{display:grid;gap:12px;padding:16px;background:var(--surface-2);border-radius:0 0 var(--radius-panel) var(--radius-panel)}
.msg{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:20px;padding:16px;border:1px solid var(--line);border-radius:var(--radius-control);background:var(--surface)}
.message-content{min-width:0;overflow-wrap:anywhere}.message-heading{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.message-side{display:flex;flex-direction:column;align-items:flex-end;gap:10px;font-size:var(--text-meta)}
.message-actions{display:flex;align-items:center;gap:8px;white-space:nowrap}.message-actions :deep(button){min-height:32px;height:32px;padding:4px 10px;font-size:var(--text-meta)}
.msg-unread{border-left:3px solid var(--discover)}.unread-tag{display:inline-flex;align-items:center;gap:6px;min-height:24px;height:24px;padding:1px 7px;font-size:12px;color:var(--discover);background:var(--discover-soft);border-color:transparent}
@media(max-width:760px){.msg{grid-template-columns:minmax(0,1fr);gap:12px}.message-side{flex-direction:row;align-items:center;flex-wrap:wrap}.message-actions{margin-left:auto}.panel-head{flex-wrap:wrap;gap:12px}}
.mini {
  height: 26px;
  padding: 0 10px;
  border-radius:var(--radius-control);
  border: 1px solid var(--line);
  background: var(--surface-3);
  color: var(--text);
  font-size: 12.5px;
  cursor: pointer;
}
.mini:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.mini:disabled {
  opacity: 0.45;
  cursor: default;
}
.mini.on {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
}
.msg-prize {
  border-color:color-mix(in srgb,var(--warn) 55%,var(--line));
}
.auto-reply{background:var(--discover-soft);color:var(--discover);border:1px solid var(--line)}
.badge-prize {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 5px;
  background: color-mix(in srgb, var(--warn) 18%, transparent);
  color: var(--warn);
}
</style>
