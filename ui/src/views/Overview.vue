<script setup lang="ts">
import { computed, ref } from 'vue'
import Icon from '../components/Icon.vue'
import {extraNav} from '../extensions'
import AccountBrowser from '../components/AccountBrowser.vue'

import { api } from '../api'
import { refreshAll, refreshGuard, state, toast } from '../store'

const busy = ref('')
const shortcuts=computed(()=>[
 {to:'/campaigns',icon:'compass',label:'发现奖品',meta:'挑选新奖品',tone:'discover'},
 {to:'/pool',icon:'queue',label:'待参与',meta:`${state.queueCount??0} 条待参与`,tone:'accent'},
 {to:'/sources',icon:'source',label:'话题与合集',meta:'查看订阅更新',tone:'discover'},
 {to:'/inbox',icon:'inbox',label:'消息',meta:`${state.unreadMessages||0} 条本机未看`,tone:'warn'},
 {to:'/tidy',icon:'trash',label:'开奖清理',meta:'整理已结束记录',tone:'warn'},
 ...extraNav.some(x=>x.to==='/digest')?[{to:'/digest',icon:'pen',label:'编稿室',meta:'整理本期奖品',tone:'editor'}]:[],
 {to:'/standby',icon:'clock',label:'挂机中心',meta:'查看多账号排期',tone:'accent'},
 {to:'/activity',icon:'source',label:'完整日志',meta:'每个来源的更新记录',tone:'discover'},
])

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
    const report = await api.settle(true, 30)
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
        <section class="panel quick-workspace">
          <div class="quick-heading"><div><span class="quick-eyebrow"><Icon name="sparkles" :size="16"/> toBili WORKSPACE</span><h2>常用操作</h2></div><div class="quick-tools"><button :disabled="!!busy" @click="scan"><Icon name="refresh" :size="16"/>{{busy==='scan'?'扫描中…':'扫描订阅'}}</button><button :disabled="!!busy" @click="settle"><Icon name="gift" :size="16"/>{{busy==='settle'?'核验中…':'核验开奖'}}</button></div></div>
          <div class="quick-grid"><RouterLink v-for="item in shortcuts" :key="item.to" :to="item.to" class="quick-link" :style="{'--quick-tone':`var(--${item.tone})`}"><span class="quick-icon"><Icon :name="item.icon" :size="23"/></span><span class="quick-copy"><strong>{{item.label}}</strong><small>{{item.meta}}</small></span><Icon class="quick-arrow" name="arrow" :size="16"/></RouterLink></div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div class="text-[14.5px] font-semibold">最近运行</div>
            <RouterLink class="text-[12px] muted" to="/activity">全部日志 ↗</RouterLink>
          </div>
          <div v-if="!state.events.length" class="px-4 py-10 text-center">
            <div class="text-[13.5px] muted">暂无运行记录</div>
            <div class="text-[12px] muted mt-1 leading-relaxed">
              暂无运行记录
            </div>
          </div>
          <div v-else class="divide-y" style="border-color: var(--line-soft)">
            <div
              v-for="(event, index) in state.events.filter(e=>e.scope==='shared'||!e.account_id||e.account_id===state.overview?.accounts.find(a=>a.current)?.id).slice(0, 6)"
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
.quick-workspace{overflow:hidden}.quick-heading{position:relative;padding:22px;min-height:128px;display:flex;align-items:center;justify-content:space-between;gap:16px;background:var(--surface-2);isolation:isolate}.quick-heading::before{content:'';position:absolute;inset:0;z-index:-1;background:url('/lottery-studio.png') center/cover;opacity:.22}.light .quick-heading::before{opacity:.85}.quick-heading h2{font-size:22px;letter-spacing:-.5px;margin-top:8px}.quick-eyebrow{display:flex;align-items:center;gap:7px;font-size:11px;font-weight:600;letter-spacing:1px;color:var(--brand)}.quick-tools{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}.quick-tools button{display:inline-flex;align-items:center;gap:6px;background:color-mix(in srgb,var(--surface) 92%,transparent);font-size:13px}.quick-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));padding:16px;gap:10px}.quick-link{display:flex;align-items:center;gap:12px;padding:14px 12px;border:1px solid var(--line-soft);border-radius:var(--radius-panel);color:var(--text);background:var(--surface);transition:transform .18s,border-color .18s,box-shadow .18s}.quick-link:hover{border-color:var(--quick-tone);transform:translateY(-2px);box-shadow:0 5px 15px color-mix(in srgb,var(--quick-tone) 12%,transparent)}.quick-icon{display:grid;place-items:center;width:43px;height:43px;border-radius:13px;flex:none;color:var(--quick-tone);background:color-mix(in srgb,var(--quick-tone) 12%,var(--surface))}.quick-copy{display:flex;flex-direction:column;gap:2px;min-width:0}.quick-copy strong{font-size:14px;font-weight:600}.quick-copy small{font-size:12px;color:var(--muted)}.quick-arrow{margin-left:auto;color:var(--muted)}
@media(max-width:1000px){.quick-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:650px){.quick-heading{flex-wrap:wrap}.quick-grid{grid-template-columns:1fr}}

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
