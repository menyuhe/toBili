<script setup lang="ts">
import { computed, ref } from 'vue'
import TaskLog from '../components/TaskLog.vue'
import {useRoute} from 'vue-router'

import { api, req } from '../api'
import { refreshAll, state, toast } from '../store'

const signingOut = ref(false)

const route=useRoute()
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
      <div class="panel-head"><h2>运行记录</h2><span class="chip" :class="state.connected?'chip-ok':''"><span class="dot"/>{{state.connected?'已连接':'重连中'}}</span></div>
      <TaskLog :issues="route.query.issues==='1'"/>
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
          <div v-else class="text-[12.5px] muted">{{state.guardError||'正在读取参与状态…'}}<button v-if="state.guardError" @click="refreshAll">重试</button></div>
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
        <button class="btn danger" :disabled="signingOut" @click="signOut">退出登录</button>
      </section>
    </aside>
  </div>
</template>

<style scoped>
.event-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8}.event-issue{color:var(--warn)}button[aria-pressed=true]{border-color:var(--accent);background:var(--accent-soft);color:var(--accent)}
</style>
