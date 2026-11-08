<script setup lang="ts">
import { computed, ref } from 'vue'
import AccountBrowser from '../components/AccountBrowser.vue'

import { api } from '../api'
import { refreshAll, refreshGuard, state, toast } from '../store'

const busy = ref('')

const cards = computed(() => {
  const overview = state.overview
  return [
    { label: '活动奖品', value: overview?.campaigns.total ?? 0, hint: '已收录的活动奖品', tone: 'text' },
    { label: '仍在进行', value: overview?.campaigns.open ?? 0, hint: '未到截止时间', tone: 'accent' },
    { label: '已参与', value: overview?.enrollments.total ?? 0, hint: '点赞 / 转发 / 评论已记账', tone: 'text' },
    { label: '已开奖', value: overview?.enrollments.settled ?? 0, hint: '结果已核验', tone: 'ok' },
  ]
})

async function scan() {
  busy.value = 'scan'
  try {
    const report = await api.scan(1, 20)
    toast(
      `扫了 ${report.scanned} 条动态，命中 ${report.matched} 条，去重后 ${report.unique} 个活动`,
      'ok',
    )
    if (report.problems.length) toast(report.problems[0], 'warn')
    await refreshAll()
  } catch (error) {
    toast(error instanceof Error ? error.message : '扫描失败', 'danger')
  } finally {
    busy.value = ''
  }
}

async function settle() {
  busy.value = 'settle'
  try {
    const report = await api.settle(false, 30)
    toast(`核验 ${report.checked} 条，有结论 ${report.decided} 条`, 'ok')
    if (report.problems.length) toast(report.problems[0], 'warn')
    await refreshAll()
  } catch (error) {
    toast(error instanceof Error ? error.message : '核验失败', 'danger')
  } finally {
    busy.value = ''
  }
}

const signals = computed(() => {
  const gate = state.gate
  if (!gate) return []
  return [
    { label: '手动暂停', value: gate.paused ? '已暂停' : '未暂停', bad: gate.paused },
    { label: '近 24h 风控', value: `${gate.risk_hits} 次`, bad: gate.risk_hits > 0 },
    {
      label: 'B 站私信',
      value: gate.readable ? `${Math.max(0,gate.unread_chat - gate.acked_chat)} 条待查看` : '读不到',
      bad: gate.readable ? gate.unread_chat > gate.acked_chat : true,
      url:'https://message.bilibili.com/#/whisper',
    },
    {
      label: 'B 站系统通知',
      value: gate.readable ? `${Math.max(0,gate.unread_system - gate.acked_system)} 条待查看` : '读不到',
      bad: gate.readable ? gate.unread_system > gate.acked_system : true,
      url:'https://message.bilibili.com/#/system',
    },
  ]
})

async function ack() {
  try {
    await api.ack()
    toast('已确认消息', 'ok')
    await refreshGuard()
  } catch (error) {
    toast(error instanceof Error ? error.message : '操作失败', 'danger')
  }
}

async function togglePause() {
  try {
    if (state.gate?.paused) {
      const result = await api.resume()
      toast(result.open ? '已恢复执行' : `清理仍暂停：${result.reason}`, result.open ? 'ok' : 'warn')
    } else {
      await api.pause('在界面上手动暂停')
      toast('已暂停所有清理动作', 'warn')
    }
    await refreshGuard()
  } catch (error) {
    toast(error instanceof Error ? error.message : '操作失败', 'danger')
  }
}
</script>

<template>
  <div class="max-w-[1180px] space-y-4">
    <section class="panel">
      <div class="grid grid-cols-2 lg:grid-cols-4 divide-x" style="border-color: var(--line-soft)">
        <div v-for="(card, index) in cards" :key="card.label" class="px-5 py-4" :style="index ? { borderLeft: '1px solid var(--line-soft)' } : {}">
          <div class="text-[12.5px] muted">{{ card.label }}</div>
          <div class="mt-1.5 flex items-baseline gap-1.5">
            <span
              class="text-[28px] font-semibold tracking-tight tabular-nums leading-none"
              :style="{ color: card.tone === 'accent' ? 'var(--accent)' : card.tone === 'ok' ? 'var(--ok)' : 'var(--text)' }"
            >{{ card.value }}</span>
          </div>
          <div class="text-[11.5px] muted mt-2">{{ card.hint }}</div>
        </div>
      </div>
    </section>

    <div class="overview-columns">
      <div class="space-y-4 min-w-0">
        <section class="panel">
          <div class="panel-head">
            <div>
              <div class="text-[14.5px] font-semibold">常用操作</div>
              
            </div>
          </div>
          <div class="p-4 grid grid-cols-1 sm:grid-cols-2 gap-3">
            <button class="action" :disabled="busy === 'scan'" @click="scan">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none">
                <path d="M4 7V4h3m10 0h3v3m0 10v3h-3M7 20H4v-3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
                <path d="M8 12h8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
              </svg>
              <span class="font-medium">{{ busy === 'scan' ? '正在扫描…' : '扫描订阅源' }}</span>
              <span class="text-[12px] muted">拉一遍关注流，识别活动奖品</span>
            </button>
            <button class="action" :disabled="busy === 'settle'" @click="settle">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none">
                <path d="M4 12.5 9 17.5 20 6.5" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
              <span class="font-medium">{{ busy === 'settle' ? '正在核验…' : '核验开奖' }}</span>
              <span class="text-[12px] muted">只读公告，有结论才落库</span>
            </button>

          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div class="text-[14.5px] font-semibold">最近运行</div>
            <span class="text-[12px] muted">来自事件流，实时</span>
          </div>
          <div v-if="!state.events.length" class="px-4 py-10 text-center">
            <div class="text-[13.5px] muted">暂无运行记录</div>
            <div class="text-[12px] muted mt-1 leading-relaxed">
              暂无运行记录
            </div>
          </div>
          <div v-else class="divide-y" style="border-color: var(--line-soft)">
            <div
              v-for="(event, index) in state.events.slice(0, 6)"
              :key="index"
              class="flex items-center gap-3 px-4 py-2.5 row-hover"
            >
              <span class="dot" :style="{ color: 'var(--ok)' }" />
              <span class="text-[13.5px] flex-1 min-w-0 truncate">{{ event.text }}</span>
              <span class="text-[11.5px] muted tabular-nums">{{ new Date(event.at * 1000).toLocaleTimeString('zh-CN') }}</span>
            </div>
          </div>
        </section>
      </div>

      <aside class="space-y-4 min-w-0">
        <section class="panel">
          <div class="panel-head">
            <div class="text-[14.5px] font-semibold">清理闸门</div>
            <span class="chip" :class="state.gate?.open ? 'chip-ok' : 'chip-danger'">
              <span class="dot" />{{ state.gate?.open ? '开启' : '关闭' }}
            </span>
          </div>
          <div class="px-4 py-3">
            <p v-if="state.gate && !state.gate.open" class="text-[12.5px] leading-relaxed mb-3" style="color: var(--danger)">
              {{ state.gate.reason }}
            </p>
            <div class="space-y-2">
              <div v-for="signal in signals" :key="signal.label" class="flex items-center justify-between text-[13px]">
                <span class="dim">{{ signal.label }}</span>
                <AccountBrowser v-if="signal.url" class="gate-message" :class="{'gate-message-pending':signal.bad}" :url="signal.url" :label="signal.value+' ↗'" />
                <span v-else class="flex items-center gap-1.5" :style="{ color: signal.bad ? 'var(--danger)' : 'var(--text-dim)' }">
                  <span class="dot" />{{ signal.value }}
                </span>
              </div>
            </div>
            <div class="flex gap-2 mt-4">
              <button class="mini-btn flex-1" @click="ack">确认已读</button>
              <button class="mini-btn flex-1" :class="{ 'mini-danger': !state.gate?.paused }" @click="togglePause">
                {{ state.gate?.paused ? '恢复执行' : '立即暂停' }}
              </button>
            </div>
          </div>
        </section>

        <section class="panel px-4 py-3.5">
          <div class="flex items-center justify-between mb-2.5">
            <div class="text-[14.5px] font-semibold">凭据</div>
            <span class="chip" :class="state.overview?.encrypted ? 'chip-ok' : 'chip-warn'">
              {{ state.overview?.encrypted ? '已加密' : '未加密' }}
            </span>
          </div>
        </section>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.gate-message{min-height:28px;padding:3px 8px;font-size:var(--text-meta);background:var(--surface)}.gate-message-pending{color:var(--danger);border-color:color-mix(in srgb,var(--danger) 40%,var(--line))}
.overview-columns{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:16px;align-items:start}.overview-columns>*{min-width:0}.overview-columns .panel{min-width:0}.overview-columns .action{white-space:normal}@media(max-width:1150px){.overview-columns{grid-template-columns:minmax(0,1fr)}}

.action {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 12px 13px;
  border-radius: 11px;
  border: 1px solid var(--line);
  background: var(--surface-2);
  color: var(--text);
  text-align: left;
  font-size: 13.5px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.action:hover:not(:disabled) {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.action:disabled {
  opacity: 0.55;
  cursor: default;
}
.action svg {
  color: var(--accent);
  margin-bottom: 2px;
}
.mini-btn {
  height: 28px;
  border-radius:var(--radius-control);
  border: 1px solid var(--line);
  background: transparent;
  color: var(--text-dim);
  font-size: 12.5px;
  cursor: pointer;
  transition: all 0.14s ease;
}
.mini-btn:hover {
  background: var(--surface-3);
  color: var(--text);
}
.mini-danger:hover {
  color: var(--danger);
  border-color: var(--danger);
  background: var(--danger-soft);
}
</style>
