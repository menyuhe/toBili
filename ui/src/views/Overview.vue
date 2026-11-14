<script setup lang="ts">
import { computed, ref } from 'vue'
import Icon from '../components/Icon.vue'
import {extraNav} from '../extensions'

import { api } from '../api'
import { refreshAll, state, toast } from '../store'

const busy = ref('')
const shortcuts=computed(()=>[
 {to:'/campaigns',icon:'compass',label:'发现奖品',meta:'挑选新奖品',tone:'discover'},
 {to:'/pool',icon:'queue',label:'待参与',meta:`${state.queueCount??0} 条待参与`,tone:'accent'},
 {to:'/sources',icon:'source',label:'话题与合集',meta:'查看订阅更新',tone:'discover'},
 {to:'/inbox',icon:'inbox',label:'消息',meta:`${state.unreadMessages||0} 条本机未看`,tone:'warn'},
 {to:'/tidy',icon:'trash',label:'开奖清理',meta:'整理已结束记录',tone:'warn'},
 ...extraNav.some(x=>x.to==='/digest')?[{to:'/digest',icon:'pen',label:'专栏运营',meta:'整理本期奖品',tone:'editor'}]:[],
 {to:'/standby',icon:'clock',label:'挂机运作',meta:'查看多账号排期',tone:'accent'},
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
    const official = report.official ?? 0
    const guessed = report.guessed ?? 0
    toast(
      `扫了 ${report.scanned} 条动态，命中 ${report.matched} 条，去重后 ${report.unique} 个活动` +
        (official || guessed ? `；官方索引确认 ${official} 条，其余 ${guessed} 条只算线索` : ''),
      guessed && !official ? 'warn' : 'ok',
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
</script>

<template>
  <div class="overview-page">
    <section class="panel">
      <div class="overview-stats">
        <div v-for="(card, index) in cards" :key="card.label" class="stat" :style="index ? { borderLeft: '1px solid var(--line-soft)' } : {}">
          <div class="stat-label">{{ card.label }}</div>
          <div class="stat-value" :style="{ color: card.tone === 'accent' ? 'var(--accent)' : card.tone === 'ok' ? 'var(--ok)' : 'var(--text)' }">{{ card.value }}</div>
          <div class="stat-hint">{{ card.hint }}</div>
        </div>
      </div>
    </section>

    <div class="overview-row">
      <section class="panel quick-workspace">
        <div class="quick-heading">
          <div><span class="quick-eyebrow"><Icon name="sparkles" :size="16"/> toBili WORKSPACE</span><h2>常用操作</h2></div>
          <div class="quick-tools"><button :disabled="!!busy" @click="scan"><Icon name="refresh" :size="16"/>{{busy==='scan'?'扫描中…':'扫描订阅'}}</button><button :disabled="!!busy" @click="settle"><Icon name="gift" :size="16"/>{{busy==='settle'?'核验中…':'核验开奖'}}</button></div>
        </div>
        <div class="quick-grid"><RouterLink v-for="item in shortcuts" :key="item.to" :to="item.to" class="quick-link" :style="{'--quick-tone':`var(--${item.tone})`}"><span class="quick-icon"><Icon :name="item.icon" :size="23"/></span><span class="quick-copy"><strong>{{item.label}}</strong><small>{{item.meta}}</small></span><Icon class="quick-arrow" name="arrow" :size="16"/></RouterLink></div>
      </section>

      <section class="panel recent-panel">
        <div class="panel-head">
          <div class="text-[14.5px] font-semibold">最近运行</div>
          <RouterLink class="text-[12px] muted" to="/activity">全部日志 ↗</RouterLink>
        </div>
        <div v-if="!state.events.length" class="empty-state">暂无运行记录</div>
        <div v-else class="recent-list">
          <div v-for="(event, index) in state.events.filter(e=>e.scope==='shared'||!e.account_id||e.account_id===state.overview?.accounts.find(a=>a.current)?.id).slice(0, 12)" :key="index" class="recent-row">
            <span class="dot" :style="{ color: 'var(--ok)' }" />
            <span class="recent-text">{{ event.text }}</span>
            <span class="recent-time">{{ new Date(event.at * 1000).toLocaleTimeString('zh-CN') }}</span>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.overview-page{max-width:1180px;margin:auto;display:flex;flex-direction:column;gap:16px}
.overview-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))}
.stat{padding:16px 20px}
.stat-label{font-size:var(--text-meta);color:var(--muted)}
.stat-value{margin-top:6px;font-size:28px;font-weight:600;letter-spacing:-.5px;line-height:1;font-variant-numeric:tabular-nums}
.stat-hint{margin-top:8px;font-size:var(--text-small);color:var(--muted)}
@media(max-width:700px){.overview-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.stat:nth-child(3){border-left:0!important}}

/* 左边常用操作，右边最近运行。清理闸门和凭证已经挪到「清理」和「设置」里，
   这一页只留"要去哪"和"刚发生了什么"。
   两栏等高：右边那条日志以前只顶到内容高度，比左边矮一截，看着像没对齐。 */
.overview-row{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:16px;align-items:stretch}
.overview-row>*{min-width:0}
@media(max-width:1150px){.overview-row{grid-template-columns:minmax(0,1fr)}}

.quick-workspace{overflow:hidden}
.quick-heading{position:relative;padding:22px;min-height:128px;display:flex;align-items:center;justify-content:space-between;gap:16px;background:var(--surface-2);isolation:isolate}
.quick-heading::before{content:'';position:absolute;inset:0;z-index:-1;background:url('/lottery-studio.png') center/cover;opacity:.22}
.light .quick-heading::before{opacity:.85}
.quick-heading h2{font-size:22px;letter-spacing:-.5px;margin-top:8px}
.quick-eyebrow{display:flex;align-items:center;gap:7px;font-size:11px;font-weight:600;letter-spacing:1px;color:var(--brand)}
.quick-tools{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}
.quick-tools button{display:inline-flex;align-items:center;gap:6px;background:color-mix(in srgb,var(--surface) 92%,transparent);font-size:var(--text-meta)}
.quick-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));padding:16px;gap:10px}
.quick-link{display:flex;align-items:center;gap:12px;padding:14px 12px;border:1px solid var(--line-soft);border-radius:var(--radius-panel);color:var(--text);background:var(--surface);transition:transform .18s,border-color .18s,box-shadow .18s}
.quick-link:hover{border-color:var(--quick-tone);transform:translateY(-2px);box-shadow:0 5px 15px color-mix(in srgb,var(--quick-tone) 12%,transparent)}
.quick-icon{display:grid;place-items:center;width:43px;height:43px;border-radius:13px;flex:none;color:var(--quick-tone);background:color-mix(in srgb,var(--quick-tone) 12%,var(--surface))}
.quick-copy{display:flex;flex-direction:column;gap:2px;min-width:0}
.quick-copy strong{font-size:var(--text-control);font-weight:600}
.quick-copy small{font-size:var(--text-small);color:var(--muted)}
.quick-arrow{margin-left:auto;color:var(--muted)}
@media(max-width:650px){.quick-heading{flex-wrap:wrap}.quick-grid{grid-template-columns:1fr}}

.recent-panel{overflow:hidden;display:flex;flex-direction:column}
.recent-list{display:flex;flex-direction:column;flex:1;min-height:0;overflow-y:auto}
.recent-row{display:flex;align-items:center;gap:10px;padding:9px 16px;border-top:1px solid var(--line-soft);min-width:0}
.recent-row:first-child{border-top:0}
.recent-text{flex:1;min-width:0;font-size:var(--text-meta);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.recent-time{flex:none;font-size:var(--text-small);color:var(--muted);font-variant-numeric:tabular-nums}
</style>
