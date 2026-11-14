<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { registerWorkGuard } from '../workGuard'

import { api, type ExecReport, type Plan, type PlanLine, type PlanSummary } from '../api'
import { refreshGuard, state, toast } from '../store'
import Modal from '../components/Modal.vue'
import Icon from '../components/Icon.vue'

const plan = ref<Plan | null>(null)
const summary = ref<PlanSummary[]>([])
const rejected = ref<PlanLine[]>([])
const checked = ref<Set<number>>(new Set())
const report = ref<ExecReport | null>(null)
const busy = ref('')
const loading = ref(true)
const selectionDirty=ref(false)
let selectionRevision=0
let selectionSave:Promise<void>=Promise.resolve()
const confirming = ref(false)

const PLAN_LABEL: Record<string, string> = {
  draft: '草稿',
  previewed: '待确认',
  confirmed: '已确认',
  running: '执行中',
  done: '已完成',
  aborted: '已中止',
}

const STEPS = [
  { key: 'previewed', label: '清单' },
  { key: 'confirmed', label: '确认' },
  { key: 'running', label: '执行' },
  { key: 'done', label: '记录' },
]

const stepIndex = computed(() => {
  const value = plan.value?.state ?? ''
  if (value === 'draft') return 0
  if (value === 'previewed') return 0
  if (value === 'confirmed') return 1
  if (value === 'running') return 2
  if (value === 'done' || value === 'aborted') return 3
  return -1
})

const items = computed(() => plan.value?.items ?? [])
const selectable = computed(() => items.value.filter((item) => item.state === 'pending' || item.state === 'dropped'))
const chosen = computed(() => selectable.value.filter((item) => checked.value.has(item.id)))
const locked = computed(() => (plan.value?.state ?? '') !== 'previewed')

async function loadPlans() {
  try {
    summary.value = (await api.plans(8)).items
  } catch {
    /* 历史列表拿不到不影响主流程 */
  }
}

async function open(id: number) {
  if(!await flushSelection())return
  busy.value = 'open'
  try {
    const body = await api.plan(id)
    plan.value = body
    checked.value = new Set(
      body.items.filter((item) => item.state === 'pending').map((item) => item.id),
    )
    report.value = null
    if (body.state === 'previewed') rejected.value = []
  } catch (error) {
    toast(error instanceof Error ? error.message : '打不开这个计划', 'danger')
  } finally {
    busy.value = ''
  }
}

async function preview() {
  if(!await flushSelection())return
  busy.value = 'preview'
  report.value = null
  rejected.value = []
  try {
    const body = await api.preview()
    rejected.value = body.items.filter((line) => !line.allowed)
    if (!body.persisted) {
      plan.value = null
      toast('暂无可清理条目', 'warn')
    } else {
      await open(body.plan_id)
      toast(`列出 ${body.clearable} 条可清理，请逐条核对`, 'ok')
    }
    await loadPlans()
  } catch (error) {
    toast(error instanceof Error ? error.message : '生成清单失败', 'danger')
  } finally {
    busy.value = ''
  }
}

// 勾选即时落库。延迟 350ms 合并连续点击，但状态以服务端为准 ——
// 本地只负责"看起来点了"，最终做几条由 select 接口回答。
let syncTimer: number | undefined
function toggle(id: number) {
  if (locked.value||busy.value) return
  const next = new Set(checked.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  checked.value = next
  selectionDirty.value=true;selectionRevision++
  window.clearTimeout(syncTimer)
  syncTimer = window.setTimeout(sync, 350)
}

async function sync(strict = false) {
  if (!plan.value || locked.value || !selectionDirty.value) { await selectionSave; return }
  const id=plan.value.plan_id, ids=[...checked.value], revision=selectionRevision
  const save=selectionSave.catch(()=>{}).then(async()=>{
    await api.select(id,ids)
    if(revision===selectionRevision)selectionDirty.value=false
  })
  selectionSave=save.catch(()=>{})
  try { await save } catch(error) {
    toast(error instanceof Error?error.message:'保存勾选失败','danger')
    if(strict)throw error
  }
}
async function flushSelection(){
  window.clearTimeout(syncTimer)
  try{await sync(true);return true}catch{return false}
}
const removeGuard=registerWorkGuard(flushSelection)
onBeforeRouteLeave(flushSelection)

function toggleAll(on: boolean) {
  if (locked.value||busy.value) return
  checked.value = on ? new Set(selectable.value.map((item) => item.id)) : new Set()
  selectionDirty.value=true;selectionRevision++
  window.clearTimeout(syncTimer)
  void sync()
}

async function confirm() {
  if (!plan.value) return
  busy.value = 'confirm'
  try {
    window.clearTimeout(syncTimer)
    await sync(true)
    await api.confirm(plan.value.plan_id)
    confirmation.value = ''
    confirming.value = false
    await open(plan.value.plan_id)
    await loadPlans()
    toast('已确认', 'ok')
  } catch (error) {
    toast(error instanceof Error ? error.message : '确认失败', 'danger')
  } finally {
    busy.value = ''
  }
}

async function execute() {
  if (!plan.value) return
  busy.value = 'apply'
  try {
    const body = await api.execute(plan.value.plan_id, true)
    toast(
      `执行完成：成功 ${body.done}，跳过 ${body.skipped}，失败 ${body.failed}`,
      body.failed ? 'warn' : 'ok',
    )
    await open(plan.value.plan_id)
    report.value = body
    await loadPlans()
    await refreshGuard()
  } catch (error) {
    const message = error instanceof Error ? error.message : '执行失败'
    toast(message, 'danger')
    await refreshGuard()
  } finally {
    busy.value = ''
  }
}

const confirmation = ref('')
const showApply = ref(false)
const canApply = computed(() => confirmation.value.trim() === '删除')

const gate = computed(() => state.gate)

function askApply() {
  confirmation.value = ''
  showApply.value = true
}

async function doApply() {
  if (!canApply.value) return
  showApply.value = false
  await execute()
}

function fmt(value: string | null) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { year:'numeric',month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',hour12:false })
}

onUnmounted(() => {window.clearTimeout(syncTimer);removeGuard()})

onMounted(async () => {
  await loadPlans()
  loading.value = false
  const latest = summary.value.find((item) => ['previewed', 'confirmed', 'running'].includes(item.state))
  if (latest) await open(latest.id)
})
</script>
<template>
  <div class="tidy-workspace">
    <!-- 标题 + 主操作 -->
    <section class="panel page-heading">
      <div>
        <h2>清理</h2>
        <span class="heading-sub">
          <template v-if="plan">计划 #{{ plan.plan_id }} · {{ PLAN_LABEL[plan.state] ?? plan.state }} · {{ items.length }} 条</template>
          <template v-else>只删已判定没中奖的转发；每条都可以单独取消勾选</template>
        </span>
      </div>
      <div class="toolbar">
        <span v-if="plan && locked && plan.state !== 'done' && plan.state !== 'aborted'" class="chip chip-danger">清单已锁定</span>
        <button class="primary" :disabled="busy === 'preview'" @click="preview">
          {{ busy === 'preview' ? '正在核对…' : plan ? '更新清单' : '生成清单' }}
        </button>
      </div>
    </section>

    <!-- 分档数字 -->
    <div class="metric-row">
      <div class="panel metric-card metric-tone-accent"><span>可清理</span><strong>{{ items.length }}</strong><small>已判定没中奖的转发</small></div>
      <div class="panel metric-card metric-tone-ok"><span>已勾选</span><strong>{{ chosen.length }}</strong><small>确认后会锁定这一批</small></div>
      <div class="panel metric-card metric-tone-discover"><span>已删除</span><strong>{{ items.filter(i => i.state === 'done').length }}</strong><small>转发已经删掉</small></div>
      <div class="panel metric-card metric-tone-danger"><span>失败</span><strong>{{ items.filter(i => i.state === 'failed').length }}</strong><small>需要重新执行一次</small></div>
    </div>

    <!-- 空态 -->
    <section v-if="!plan && !loading" class="panel empty-panel">
      <Icon name="trash" :size="22" />
      <div class="text-[15px] font-semibold">还没有清理清单</div>
      <p class="text-[13px] muted">点「生成清单」，把已判定没中奖、且转发还在的条目列出来。</p>
      <button class="btn" :disabled="busy === 'preview'" @click="preview">生成清单</button>
    </section>

    <!-- 清单 -->
    <section v-if="plan" class="panel list-panel">
      <div class="panel-head tidy-head">
        <div class="steps">
          <template v-for="(step, index) in STEPS" :key="step.key">
            <div class="step">
              <div class="step-dot" :class="index <= stepIndex ? 'step-on' : ''">
                <svg v-if="index < stepIndex" width="11" height="11" viewBox="0 0 24 24" fill="none">
                  <path d="M5 13l4 4L19 7" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                <span v-else>{{ index + 1 }}</span>
              </div>
              <span class="step-label" :class="{ 'step-label-on': index <= stepIndex, 'step-label-now': index === stepIndex }">{{ step.label }}</span>
            </div>
            <div v-if="index < STEPS.length - 1" class="step-line" :class="{ 'step-line-on': index < stepIndex }" />
          </template>
        </div>
        <div v-if="!locked" class="toolbar">
          <button class="mini" @click="toggleAll(true)">全选</button>
          <button class="mini" @click="toggleAll(false)">全不选</button>
        </div>
      </div>

      <div class="list-scroll">
        <table class="dense tidy-table">
          <thead><tr><th class="pick-col"></th><th class="up-col">UP</th><th class="cell-align-start">活动奖品</th><th class="dyn-col">动态</th><th class="state-col">状态</th><th class="cell-align-start reason-col">说明</th></tr></thead>
          <tbody>
            <tr v-for="item in items" :key="item.id" :class="{ 'row-off': item.state === 'dropped' }">
              <td class="pick-col">
                <button
                  class="box"
                  role="checkbox"
                  :aria-checked="checked.has(item.id)"
                  :aria-label="'选择 '+(item.reward||item.dyn_id)"
                  :class="checked.has(item.id) ? 'box-on' : ''"
                  :disabled="!!busy || locked || item.state === 'done' || item.state === 'failed' || item.state === 'skipped'"
                  @click="toggle(item.id)"
                >
                  <svg v-if="checked.has(item.id)" width="11" height="11" viewBox="0 0 24 24" fill="none">
                    <path d="M5 13l4 4L19 7" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" />
                  </svg>
                </button>
              </td>
              <td class="up-col">{{ item.up_name || '—' }}</td>
              <td class="cell-align-start prize-cell">{{ item.reward || '奖品待补全' }}</td>
              <td class="dyn-col"><span class="kbd">{{ item.dyn_id }}</span></td>
              <td class="state-col">
                <span class="chip" :class="{
                  'chip-ok': item.state === 'done',
                  'chip-danger': item.state === 'failed',
                  'chip-warn': item.state === 'skipped',
                  'chip-accent': item.state === 'pending',
                }">
                  {{ { pending: '待执行', dropped: '已取消', done: '已删除', failed: '失败', skipped: '跳过' }[item.state] ?? item.state }}
                </span>
              </td>
              <td class="cell-align-start reason-col" :title="item.reason">
                <template v-if="item.reason">{{ item.reason }}</template>
                <template v-else-if="item.decided_at">未中奖 · {{ fmt(item.decided_at) }} 判定</template>
                <template v-else>—</template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <footer class="list-footer tidy-footer">
        <div class="gate-line">
          <span class="dot" :style="{ color: gate?.open ? 'var(--ok)' : 'var(--danger)' }" />
          <template v-if="!gate">闸门状态未知</template>
          <template v-else-if="gate.paused">已手动暂停，执行会被拒绝</template>
          <template v-else-if="!gate.open">{{ gate.reason }}</template>
          <template v-else>闸门开启，可以执行</template>
        </div>
        <div class="toolbar">
          <button v-if="plan.state === 'previewed'" class="primary" :disabled="busy === 'confirm' || !chosen.length || locked" @click="confirming = true">确认这 {{ chosen.length }} 条</button>
          <template v-else-if="plan.state === 'confirmed'">
            <button class="btn btn-danger" :disabled="!!busy || !gate?.open" @click="askApply">{{ busy === 'apply' ? '执行中…' : '真的执行' }}</button>
          </template>
          <button v-else-if="plan.state === 'done' || plan.state === 'aborted'" class="btn" :disabled="busy === 'preview'" @click="preview">更新清单</button>
        </div>
      </footer>
    </section>

    <!-- 真删的二次确认 -->
    <Modal v-if="confirming && plan" title="确认这一批清单？" @close="confirming = false">
      <p class="text-[13px] dim leading-relaxed">确认后这 {{ chosen.length }} 条会被锁定，不能再改。真正的删除还要再点一次，并且要过闸门检查。</p>
      <div class="confirm-list">
        <div v-for="item in chosen" :key="item.id" class="confirm-row"><span class="dot" style="color: var(--accent)" /><span class="truncate flex-1">{{ item.reward || item.up_name }}</span><span class="kbd">{{ item.dyn_id }}</span></div>
      </div>
      <div class="modal-actions"><button class="btn" @click="confirming = false">再想想</button><button class="btn btn-accent" :disabled="busy === 'confirm'" @click="confirm">确认</button></div>
    </Modal>

    <Modal v-if="plan && plan.state === 'confirmed' && showApply" title="这一步会真的删除" @close="showApply = false">
      <p class="text-[13px] dim leading-relaxed">删除的是你自己空间里转发的那 {{ chosen.length }} 条动态。B 站没有回收站，删掉之后找不回来。取关那部分倒是可以重新关注，但关注时间和分组不会回来。</p>
      <p class="text-[13px] mt-3 mb-1.5">请输入 <span class="font-semibold" style="color: var(--danger)">删除</span> 两个字继续</p>
      <input v-model="confirmation" class="ipt" placeholder="删除" @keyup.enter="canApply && doApply()" />
      <div class="modal-actions"><button class="btn" @click="showApply = false">取消</button><button class="btn btn-danger" :disabled="!canApply || !!busy" @click="doApply">确认删除</button></div>
    </Modal>

    <!-- 这次不动 -->
    <section v-if="rejected.length" class="panel">
      <div class="panel-head">
        <div class="text-[14.5px] font-semibold">这次不动的记录</div>
        <span class="text-[12px] muted">共 {{ rejected.length }} 条 · 每条都写了原因</span>
      </div>
      <div class="plain-list">
        <div v-for="line in rejected" :key="line.dyn_id" class="plain-row">
          <span class="kbd w-[130px] flex-none truncate">{{ line.dyn_id }}</span>
          <span class="text-[13px] dim w-[100px] flex-none truncate">{{ line.up_name || '—' }}</span>
          <span class="text-[12.5px] muted flex-1">{{ line.reason }}</span>
        </div>
      </div>
    </section>

    <!-- 执行结果 -->
    <section v-if="report" class="panel enter">
      <div class="panel-head">
        <div class="flex items-center gap-3">
          <div class="text-[14.5px] font-semibold">执行结果</div>
          <span class="chip chip-ok">成功 {{ report.done }}</span>
          <span v-if="report.skipped" class="chip">跳过 {{ report.skipped }}</span>
          <span v-if="report.failed" class="chip chip-danger">失败 {{ report.failed }}</span>
        </div>
        <span class="text-[12px] muted">{{ report.note }}</span>
      </div>
      <div class="plain-list">
        <div v-for="line in report.items" :key="line.item_id" class="plain-row">
          <span class="w-1.5 h-1.5 rounded-full flex-none" :style="{ background: line.state === 'done' ? 'var(--ok)' : line.state === 'failed' ? 'var(--danger)' : line.state === 'planned' ? 'var(--accent)' : 'var(--muted)' }" />
          <span class="text-[13px] w-[100px] flex-none truncate">{{ line.up_name || '—' }}</span>
          <span class="kbd w-[130px] flex-none truncate">{{ line.dyn_id }}</span>
          <span class="text-[12.5px] muted flex-1 truncate">{{ line.message }}</span>
        </div>
      </div>
      <div v-if="report.snapshot" class="snapshot-note">执行前已备份到 <span class="kbd">{{ report.snapshot }}</span> —— 删转发回不来，这份文件是事后唯一能核对"当时清单长什么样"的东西。</div>
    </section>

    <!-- 历史计划 -->
    <section v-if="summary.length > 1" class="panel">
      <div class="panel-head"><div class="text-[14.5px] font-semibold">历史计划</div></div>
      <div class="plain-list">
        <button v-for="row in summary" :key="row.id" class="plain-row hist-row" :class="plan && plan.plan_id === row.id ? 'hist-on' : ''" @click="open(row.id)">
          <span class="kbd w-10 flex-none">#{{ row.id }}</span>
          <span class="chip" :class="row.state === 'done' ? 'chip-ok' : row.state === 'aborted' ? 'chip-warn' : ''">{{ PLAN_LABEL[row.state] ?? row.state }}</span>
          <span class="text-[12.5px] muted flex-1 truncate">{{ row.note || `共 ${row.total} 条` }}</span>
          <span class="text-[12px] muted tabular-nums flex-none">已完成 {{ row.done }} / {{ row.total }}</span>
          <span class="text-[11.5px] muted w-[92px] text-right flex-none">{{ fmt(row.created_at) }}</span>
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.tidy-workspace{max-width:1440px;margin:auto;display:flex;flex-direction:column;gap:18px;padding-bottom:8px}

/* 空态：一句说明 + 一个按钮，不要一整片白。 */
.empty-panel{display:grid;justify-items:center;gap:10px;padding:56px 24px;text-align:center;color:var(--muted)}
.empty-panel :deep(.ui-icon){color:var(--accent)}
.empty-panel p{max-width:420px;line-height:1.7}

/* 四步状态条挪到列表头里：它说明的是"这一批清单走到哪了"，
   单独占一整块面板只会把表格往下推。 */
.tidy-head{gap:16px;flex-wrap:wrap}
.steps{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.step{display:flex;align-items:center;gap:7px}
.step-dot{width:20px;height:20px;border-radius:var(--radius-pill);display:grid;place-items:center;font-size:var(--text-small);font-weight:600;border:1px solid var(--line);color:var(--muted);background:var(--surface-2)}
.step-on{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.step-label{font-size:var(--text-meta);color:var(--muted)}
.step-label-on{color:var(--text)}
.step-label-now{font-weight:600}
.step-line{width:26px;height:1px;margin:0 3px;background:var(--line)}
.step-line-on{background:var(--accent)}

.tidy-table{width:100%;min-width:960px;table-layout:fixed}
.tidy-table td,.tidy-table th{white-space:normal;overflow:visible}
.tidy-table tbody td{border-bottom:1px solid var(--line-soft)}
.tidy-table tbody tr:last-child td{border-bottom:0}
.tidy-table tbody tr:hover{background:var(--surface-2)}
.tidy-table .row-off{opacity:.42}
.pick-col{width:52px;padding-inline:10px!important}
.up-col{width:130px;color:var(--text-dim)}
.prize-cell{min-width:260px;font-weight:600;line-height:1.6;padding-inline:14px!important}
.dyn-col{width:140px}
.state-col{width:104px}
.reason-col{width:300px;color:var(--muted);line-height:1.6;padding-inline:14px!important}

.tidy-footer{justify-content:space-between;flex-wrap:wrap}
.gate-line{display:flex;align-items:center;gap:8px;font-size:var(--text-meta);color:var(--text-dim);min-width:0}

/* 勾选框。原生 checkbox 在深色里是一块白，自己画。 */
.box{width:20px;height:20px;min-height:0;padding:0;margin:auto;flex:none;border-radius:var(--radius-tiny);border:1.5px solid var(--line);background:transparent;display:grid;place-items:center;cursor:pointer;transition:all .12s ease}
.box:hover:not(:disabled){border-color:var(--accent)}
.box-on{background:var(--accent);border-color:var(--accent);color:var(--bg)}
.box-on:hover:not(:disabled){color:var(--bg)}
:global(html.light) .box-on,:global(html.light) .box-on:hover:not(:disabled){color:#fff}
.box:disabled{cursor:default;opacity:.7}

/* 列表型块（这次不动 / 执行结果 / 历史计划）：行与行之间只留一条线。 */
.plain-list{display:flex;flex-direction:column}
.plain-row{display:flex;align-items:center;gap:12px;width:100%;padding:10px 18px;border-top:1px solid var(--line-soft);text-align:left}
.plain-row:first-child{border-top:0}
.plain-row:hover{background:var(--surface-2)}
.hist-row{background:transparent;border-left:0;border-right:0;border-bottom:0;border-radius:0;color:inherit;font:inherit;cursor:pointer}
.hist-on{background:var(--accent-soft);box-shadow:inset 2px 0 0 var(--accent)}
.hist-on:hover{background:var(--accent-soft)}

.snapshot-note{padding:10px 18px;font-size:var(--text-small);color:var(--muted);border-top:1px solid var(--line-soft)}
.confirm-list{margin-top:14px;padding:10px;border-radius:var(--radius-control);background:var(--surface-2);max-height:180px;overflow:auto}
.confirm-row{display:flex;align-items:center;gap:8px;padding:3px 0;font-size:var(--text-meta)}
</style>
