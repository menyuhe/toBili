<script setup lang="ts">
import { computed, ref, onMounted, watch } from 'vue'
import {useRoute} from 'vue-router'

import { api, req } from '../api'
import { refreshAll, refreshHistory, state, toast, describeEvent } from '../store'

const signingOut = ref(false)

const route=useRoute()
const issuesOnly=ref(route.query.issues==='1'),issueRows=ref<typeof state.events>([]),loadingIssues=ref(false)
watch(()=>route.query.issues,()=>{issuesOnly.value=route.query.issues==='1';if(issuesOnly.value)void loadIssues()})
async function loadIssues(more=false){loadingIssues.value=true;try{const before=more?(issueRows.value.at(-1)?.seq||0):0;const data=await req<{items:{seq:number;topic:string;at:string;payload:Record<string,unknown>}[]}>(`/api/history?issues_only=true&limit=100&before_seq=${before}`);const rows=data.items.map(r=>({seq:r.seq,topic:r.topic,at:Date.parse(r.at)/1000,text:describeEvent(r.topic,r.payload),issue:true}));issueRows.value=more?[...issueRows.value,...rows]:rows}catch(e){toast(String(e),'danger')}finally{loadingIssues.value=false}}
function refreshLogs(){void refreshHistory();void loadIssues()}
onMounted(()=>void loadIssues())
const events = computed(() => issuesOnly.value?issueRows.value:state.events.filter(e=>!e.account_id||e.account_id===state.overview?.accounts.find(a=>a.current)?.id))

const quota = computed(() => state.quota)

const bars = computed(() => {
  const value = quota.value
  if (!value) return []
  return [
    { label: '最近 1 小时', used: value.last_hour, total: value.budget.per_hour },
    { label: '最近 24 小时', used: value.last_day, total: value.budget.per_day },
  ]
})

async function signOut() {
  signingOut.value = true
  try {
    await api.signOut()
    toast('已退出登录，凭据已从本机删除', 'ok')
    await refreshAll()
  } catch (error) {
    toast(error instanceof Error ? error.message : '退出失败', 'danger')
  } finally {
    signingOut.value = false
  }
}

function clock(at: number) {
  return new Date(at * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}
</script>

<template>
  <div class="max-w-[1180px] grid grid-cols-1 lg:grid-cols-[1fr_340px] gap-4 items-start">
    <section class="panel">
      <div class="panel-head">
        <div class="text-[14.5px] font-semibold">运行记录</div><div class="toolbar"><button class="btn" :aria-pressed="!issuesOnly" @click="issuesOnly=false">全部</button><button class="btn" :aria-pressed="issuesOnly" @click="issuesOnly=true;loadIssues()">异常 / 待核对</button><button class="btn" @click="refreshLogs">刷新</button></div>
        <span class="chip" :class="state.connected ? 'chip-ok' : ''">
          <span class="dot" />{{ state.connected ? '已连接' : '重连中' }}
        </span>
      </div>

      <p v-if="state.historyError" class="warning">{{state.historyError}}</p>
      <div v-if="!events.length" class="px-5 py-14 text-center">
        <div class="text-[13.5px] muted">还没有事件</div>
      </div>

      <div v-else class="px-5 py-4">
        <div class="relative pl-4" style="border-left: 1px solid var(--line)">
          <div v-for="(event, index) in events" :key="index" class="relative pb-3 last:pb-0">
            <span
              class="absolute -left-[21px] top-[5px] w-[7px] h-[7px] rounded-full ring-2"
              :style="{
                background: event.topic === 'settled' ? 'var(--ok)' : event.topic.includes('fail') ? 'var(--danger)' : 'var(--accent)',
                '--tw-ring-color': 'var(--surface)',
              }"
            />
            <div class="flex items-baseline gap-2.5">
              <span class="text-[11.5px] muted tabular-nums w-[62px] flex-none">{{ clock(event.at) }}</span>
              <span class="text-[13.5px] flex-1 event-text" :class="{'event-issue':event.issue}">{{ event.text }}</span>
            </div>
          </div>
        </div>
      </div>
      <div v-if="issuesOnly&&issueRows.length" class="p-4"><button :disabled="loadingIssues" @click="loadIssues(true)">更早记录</button></div>
    </section>

    <aside class="space-y-4">
      <section class="panel">
        <div class="panel-head"><div class="text-[14.5px] font-semibold">参与节奏</div></div>
        <div class="p-4 space-y-3.5">
          <template v-if="quota">
            <div>
              <div class="flex items-center justify-between text-[12.5px] mb-1.5">
                <span class="dim">状态</span>
                <span :style="{ color: quota.allowed ? 'var(--ok)' : 'var(--warn)' }">
                  {{ quota.allowed ? '可以写' : '需要等' }}
                </span>
              </div>
              <p class="text-[12px] muted leading-relaxed">{{ quota.explain }}</p>
            </div>
            <div v-for="bar in bars" :key="bar.label">
              <div class="flex items-center justify-between text-[12.5px] mb-1.5">
                <span class="dim">{{ bar.label }}</span>
                <span class="tabular-nums muted">{{ bar.used }}{{bar.total ? ` / ${bar.total}` : ' 次'}}</span>
              </div>
              <div v-if="bar.total" class="h-1.5 rounded-full overflow-hidden" style="background: var(--surface-3)">
                <div
                  class="h-full rounded-full transition-all"
                  :style="{
                    width: `${Math.min(100, (bar.used / Math.max(1, bar.total)) * 100)}%`,
                    background: bar.used >= bar.total ? 'var(--danger)' : 'var(--accent)',
                  }"
                />
              </div>
            </div>
            <div class="text-[11.5px] muted pt-1 border-t" style="border-color: var(--line-soft)">
              活动间隔 {{ quota.budget.min_gap_seconds }} 秒
            </div>
          </template>
          <div v-else class="text-[12.5px] muted">读取中…</div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head"><div class="text-[14.5px] font-semibold">闸门判据</div></div>
        <div class="px-4 py-3 space-y-2 text-[12.5px]">
          <div class="flex items-center justify-between">
            <span class="dim">手动暂停</span>
            <span :style="{ color: state.gate?.paused ? 'var(--danger)' : 'var(--text-dim)' }">
              {{ state.gate?.paused ? '是' : '否' }}
            </span>
          </div>
          <div class="flex items-center justify-between">
            <span class="dim">风控计数</span>
            <span :style="{ color: state.gate?.risk_hits ? 'var(--danger)' : 'var(--text-dim)' }">
              {{ state.gate?.risk_hits ?? 0 }}
            </span>
          </div>
          <div class="flex items-center justify-between">
            <span class="dim">消息可读</span>
            <span :style="{ color: state.gate?.readable ? 'var(--text-dim)' : 'var(--danger)' }">
              {{ state.gate?.readable ? '是' : '读不到（按关闭处理）' }}
            </span>
          </div>
          <div class="flex items-center justify-between">
            <span class="dim">读过的消息</span>
            <span class="muted">
              私信 {{ state.gate?.acked_chat ?? 0 }} · 通知 {{ state.gate?.acked_system ?? 0 }}
            </span>
          </div>
        </div>
      </section>

      <section class="panel px-4 py-3.5">
        <div class="text-[14.5px] font-semibold mb-1.5">本机凭据</div>
        <p class="text-[12.5px] muted leading-relaxed mb-3">
          退出登录会删掉本机保存的 cookie。下次要用得重新扫码。
        </p>
        <button class="btn" :disabled="signingOut" @click="signOut">退出登录</button>
      </section>
    </aside>
  </div>
</template>

<style scoped>
.event-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8}.event-issue{color:var(--warn)}button[aria-pressed=true]{border-color:var(--accent);background:var(--accent-soft);color:var(--accent)}
.btn {
  height: 31px;
  padding: 0 13px;
  border-radius:var(--radius-control);
  border: 1px solid var(--line);
  background: var(--surface-3);
  color: var(--text);
  font-size:var(--text-meta);
  cursor: pointer;
  transition: all 0.14s ease;
}
.btn:hover:not(:disabled) {
  border-color: var(--danger);
  color: var(--danger);
}
.btn:disabled {
  opacity: 0.5;
}
</style>
