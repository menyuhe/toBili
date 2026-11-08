<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { registerWorkGuard } from '../workGuard'

import { api, type ExecReport, type Plan, type PlanLine, type PlanSummary } from '../api'
import { refreshGuard, state, toast } from '../store'

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
  <div class="max-w-[1180px] space-y-4">
    <!-- 状态条：四步是硬的，这里只负责让人看清走到哪儿了 -->
    <section class="panel px-5 py-4">
      <div class="flex items-center justify-between gap-6 flex-wrap">
        <div class="flex items-center gap-1">
          <template v-for="(step, index) in STEPS" :key="step.key">
            <div class="flex items-center gap-1.5">
              <div class="step-dot" :class="index <= stepIndex ? 'step-on' : ''">
                <svg v-if="index < stepIndex" width="11" height="11" viewBox="0 0 24 24" fill="none">
                  <path d="M5 13l4 4L19 7" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                <span v-else>{{ index + 1 }}</span>
              </div>
              <span class="text-[13px]" :style="{ color: index <= stepIndex ? 'var(--text)' : 'var(--muted)', fontWeight: index === stepIndex ? 600 : 400 }">
                {{ step.label }}
              </span>
            </div>
            <div v-if="index < STEPS.length - 1" class="w-8 h-px mx-1" :style="{ background: index < stepIndex ? 'var(--accent)' : 'var(--line)' }" />
          </template>
        </div>

        <div class="flex items-center gap-2">
          <span v-if="plan" class="chip" :class="plan.state === 'done' ? 'chip-ok' : plan.state === 'aborted' ? 'chip-warn' : 'chip-accent'">
            #{{ plan.plan_id }} · {{ PLAN_LABEL[plan.state] ?? plan.state }}
          </span>
          <span v-if="plan && locked && plan.state !== 'done' && plan.state !== 'aborted'" class="chip chip-danger">
            清单已锁定
          </span>
          <button class="btn" :disabled="busy === 'preview'" @click="preview">
            {{ busy === 'preview' ? '正在核对…' : plan ? '更新清单' : '生成清单' }}
          </button>
        </div>
      </div>
    </section>

    <!-- 空态 -->
    <section v-if="!plan && !loading" class="panel px-6 py-14 text-center">
      <div class="w-11 h-11 rounded-xl grid place-items-center mx-auto mb-4" style="background: var(--accent-soft); color: var(--accent)">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
          <path d="M4 7h16M9 7V5h6v2m-8 0 1 13h8l1-13" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </div>
      <div class="text-[15px] font-semibold">还没有清理清单</div>
      <p class="text-[13px] muted mt-2 leading-relaxed max-w-[440px] mx-auto">
        暂无清理计划
      </p>
      <button class="btn mt-5" :disabled="busy === 'preview'" @click="preview">生成清单</button>
    </section>

    <!-- 清单 -->
    <section v-if="plan" class="panel overflow-hidden">
      <div class="panel-head">
        <div class="flex items-center gap-3">
          <div class="text-[14.5px] font-semibold">要清理的转发</div>
          <span class="text-[12px] muted">
            {{ items.length }} 条 · 已勾选 <span style="color: var(--accent)">{{ chosen.length }}</span>
          </span>
        </div>
        <div v-if="!locked" class="flex items-center gap-2">
          <button class="link-btn" @click="toggleAll(true)">全选</button>
          <span class="text-[11px] muted">/</span>
          <button class="link-btn" @click="toggleAll(false)">全不选</button>
        </div>
      </div>

      <div class="overflow-x-auto">
        <table class="tidy-table w-full text-[13.5px]">
          <thead>
            <tr class="text-[12px] muted" style="background: var(--surface-2)">
              <th class="w-10 px-3 py-2.5 text-left font-normal"></th>
              <th class="px-2 py-2.5 text-left font-normal">UP</th>
              <th class="px-2 py-2.5 text-left font-normal">活动奖品</th>
              <th class="px-2 py-2.5 text-left font-normal">动态</th>
              <th class="px-2 py-2.5 text-left font-normal whitespace-nowrap">状态</th>
              <th class="px-3 py-2.5 text-left font-normal">说明</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in items"
              :key="item.id"
              class="border-t row-hover"
              :style="[
                { borderColor: 'var(--line-soft)' },
                item.state === 'dropped' ? { opacity: 0.42 } : {},
              ]"
            >
              <td class="px-3 py-2.5">
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
              <td class="px-2 py-2.5 dim truncate max-w-[110px]">{{ item.up_name || '—' }}</td>
              <td class="px-2 py-2.5 truncate max-w-[280px]">{{ item.reward || '奖品待补全' }}</td>
              <td class="px-2 py-2.5"><span class="kbd">{{ item.dyn_id }}</span></td>
              <td class="px-2 py-2.5 whitespace-nowrap">
                <span class="chip" :class="{
                  'chip-ok': item.state === 'done',
                  'chip-danger': item.state === 'failed',
                  'chip-warn': item.state === 'skipped',
                  'chip-accent': item.state === 'pending',
                }">
                  {{
                    { pending: '待执行', dropped: '已取消', done: '已删除', failed: '失败', skipped: '跳过' }[
                      item.state
                    ] ?? item.state
                  }}
                </span>
              </td>
              <td class="px-3 py-2.5 muted text-[12.5px] truncate max-w-[240px]" :title="item.reason">
                <template v-if="item.reason">{{ item.reason }}</template>
                <template v-else-if="item.decided_at">未中奖 · {{ fmt(item.decided_at) }} 判定</template>
                <template v-else>—</template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 执行按钮区 -->
      <div class="px-4 py-3 flex items-center justify-between gap-3 flex-wrap border-t" style="border-color: var(--line-soft); background: var(--surface-2)">
        <div class="text-[12px] muted flex items-center gap-1.5">
          <span class="dot" :style="{ color: gate?.open ? 'var(--ok)' : 'var(--danger)' }" />
          <template v-if="!gate">闸门状态未知</template>
          <template v-else-if="gate.paused">已手动暂停，执行会被拒绝</template>
          <template v-else-if="!gate.open">{{ gate.reason }}</template>
          <template v-else>闸门开启，可以执行</template>
        </div>

        <div class="flex items-center gap-2">
          <button v-if="plan.state === 'previewed'" class="btn" :disabled="busy === 'confirm' || !chosen.length || locked" @click="confirming = true">
            确认这 {{ chosen.length }} 条
          </button>
          <template v-else-if="plan.state === 'confirmed'">
            
            <button class="btn btn-danger" :disabled="!!busy || !gate?.open" @click="askApply">
              {{ busy === 'apply' ? '执行中…' : '真的执行' }}
            </button>
          </template>
          <button v-else-if="plan.state === 'done' || plan.state === 'aborted'" class="btn" :disabled="busy === 'preview'" @click="preview">
            更新清单
          </button>
        </div>
      </div>
    </section>

    <!-- 真删的二次确认 -->
    <Teleport to="body">
      <div v-if="confirming && plan" class="overlay">
        <div class="dialog enter">
          <div class="text-[16px] font-semibold" style="color: var(--danger)">确认这一批清单？</div>
          <p class="text-[13px] dim mt-2.5 leading-relaxed">
            确认后这 {{ chosen.length }} 条会被锁定，不能再改。<br />
            真正的删除还要再点一次，并且要过闸门检查。
          </p>
          <div class="mt-3 p-2.5 rounded-lg max-h-[150px] overflow-auto text-[12.5px]" style="background: var(--surface-2)">
            <div v-for="item in chosen" :key="item.id" class="flex items-center gap-2 py-0.5">
              <span class="dot" style="color: var(--accent)" />
              <span class="truncate flex-1">{{ item.reward || item.up_name }}</span>
              <span class="kbd">{{ item.dyn_id }}</span>
            </div>
          </div>
          <div class="flex justify-end gap-2 mt-4">
            <button class="btn" @click="confirming = false">再想想</button>
            <button class="btn btn-accent" :disabled="busy === 'confirm'" @click="confirm">确认</button>
          </div>
        </div>
      </div>

      <div v-if="plan && plan.state === 'confirmed' && showApply" class="overlay">
        <div class="dialog enter" style="max-width: 400px">
          <div class="text-[16px] font-semibold" style="color: var(--danger)">这一步会真的删除</div>
          <p class="text-[13px] dim mt-2.5 leading-relaxed">
            删除的是你自己空间里转发的那 {{ chosen.length }} 条动态。B 站没有回收站，
            删掉之后找不回来。<br />
            取关那部分倒是可以重新关注，但关注时间和分组不会回来。
          </p>
          <p class="text-[13px] mt-3 mb-1.5" style="color: var(--text)">
            请输入 <span class="font-semibold" style="color: var(--danger)">删除</span> 两个字继续
          </p>
          <input v-model="confirmation" class="ipt" placeholder="删除" @keyup.enter="canApply && doApply()" />
          <div class="flex justify-end gap-2 mt-4">
            <button class="btn" @click="showApply = false">取消</button>
            <button class="btn btn-danger" :disabled="!canApply || !!busy" @click="doApply">确认删除</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 这次不动 -->
    <section v-if="rejected.length" class="panel">
      <div class="panel-head">
        <div class="text-[14.5px] font-semibold">这次不动的记录</div>
        <span class="text-[12px] muted">共 {{ rejected.length }} 条 · 每条都写了原因</span>
      </div>
      <div class="divide-y" style="border-color: var(--line-soft)">
        <div v-for="line in rejected" :key="line.dyn_id" class="flex items-center gap-3 px-4 py-2">
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
      <div class="divide-y" style="border-color: var(--line-soft)">
        <div v-for="line in report.items" :key="line.item_id" class="flex items-center gap-3 px-4 py-2.5">
          <span
            class="w-1.5 h-1.5 rounded-full flex-none"
            :style="{ background: line.state === 'done' ? 'var(--ok)' : line.state === 'failed' ? 'var(--danger)' : line.state === 'planned' ? 'var(--accent)' : 'var(--muted)' }"
          />
          <span class="text-[13px] w-[100px] flex-none truncate">{{ line.up_name || '—' }}</span>
          <span class="kbd w-[130px] flex-none truncate">{{ line.dyn_id }}</span>
          <span class="text-[12.5px] muted flex-1 truncate">{{ line.message }}</span>
        </div>
      </div>
      <div v-if="report.snapshot" class="px-4 py-2 text-[11.5px] muted" style="border-top: 1px solid var(--line-soft)">
        执行前已备份到 <span class="kbd">{{ report.snapshot }}</span> —— 删转发回不来，这份文件是事后唯一能核对"当时清单长什么样"的东西。
      </div>
    </section>

    <!-- 历史计划 -->
    <section v-if="summary.length > 1" class="panel">
      <div class="panel-head"><div class="text-[14.5px] font-semibold">历史计划</div></div>
      <div class="divide-y" style="border-color: var(--line-soft)">
        <button
          v-for="row in summary"
          :key="row.id"
          class="w-full flex items-center gap-3 px-4 py-2.5 text-left row-hover hist-row"
          :class="plan && plan.plan_id === row.id ? 'hist-on' : ''"
          @click="open(row.id)"
        >
          <span class="kbd w-10 flex-none">#{{ row.id }}</span>
          <span class="chip" :class="row.state === 'done' ? 'chip-ok' : row.state === 'aborted' ? 'chip-warn' : ''">
            {{ PLAN_LABEL[row.state] ?? row.state }}
          </span>
          <span class="text-[12.5px] muted flex-1 truncate">{{ row.note || `共 ${row.total} 条` }}</span>
          <span class="text-[12px] muted tabular-nums flex-none">
            已完成 {{ row.done }} / {{ row.total }}
          </span>
          <span class="text-[11.5px] muted w-[92px] text-right flex-none">{{ fmt(row.created_at) }}</span>
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* 关掉 preflight 之后，原生 button 会带着浏览器默认的浅灰底。
   历史计划那几行是 <button>，不显式清掉就会在深色界面里白一块。 */
.hist-row {
  background: transparent;
  border: none;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
/* 当前打开的那一条用左侧色条标记。只靠背景色区分，在深色里差 3% 亮度根本看不出来。 */
.hist-on {
  background: var(--accent-soft);
  box-shadow: inset 2px 0 0 var(--accent);
}
.step-dot {
  width: 20px;
  height: 20px;
  border-radius:var(--radius-pill);
  display: grid;
  place-items: center;
  font-size: 11.5px;
  font-weight: 600;
  border: 1px solid var(--line);
  color: var(--muted);
  background: var(--surface-2);
}
.step-on {
  background: var(--accent);
  border-color: var(--accent);
  color: #0a0c10;
}
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 31px;
  padding: 0 13px;
  border-radius:var(--radius-control);
  border: 1px solid var(--line);
  background: var(--surface-3);
  color: var(--text);
  font-size:var(--text-meta);
  font-weight: 500;
  cursor: pointer;
  transition: all 0.14s ease;
}
.btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.btn:disabled {
  opacity: 0.45;
  cursor: default;
}
.btn-accent {
  background: var(--accent);
  border-color: var(--accent);
  color: #0a0c10;
  font-weight: 600;
}
.btn-accent:hover:not(:disabled) {
  color: #0a0c10;
  filter: brightness(1.07);
}
.btn-danger {
  border-color: var(--danger);
  color: var(--danger);
  background: var(--danger-soft);
}
.btn-danger:hover:not(:disabled) {
  color: #fff;
  background: var(--danger);
  border-color: var(--danger);
}
.link-btn {
  border: none;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 5px;
}
.link-btn:hover {
  color: var(--accent);
  background: var(--accent-soft);
}
.box {
  width: 20px;
  height: 20px;
  min-height:0;
  padding:0;
  margin:auto;
  flex:none;
  border-radius: 5px;
  border: 1.5px solid var(--line);
  background: transparent;
  display: grid;
  place-items: center;
  cursor: pointer;
  transition: all 0.12s ease;
}
.box:hover:not(:disabled) {
  border-color: var(--accent);
}
.box-on {
  background: var(--accent);
  border-color: var(--accent);
  color:var(--bg);
}
.box-on:hover:not(:disabled){color:var(--bg)}
:global(html.light) .box-on,:global(html.light) .box-on:hover:not(:disabled){color:#fff}
.box:disabled {
  cursor: default;
  opacity: 0.7;
}
.overlay {
  position: fixed;
  inset: 0;
  z-index: 9000;
  display: grid;
  place-items: center;
  background: rgba(6, 8, 11, 0.62);
  backdrop-filter: blur(2px);
}
.dialog {
  width: 440px;
  max-width: calc(100vw - 32px);
  padding: 20px;
  border-radius: 14px;
  background: var(--surface);
  border: 1px solid var(--line);
  box-shadow: 0 24px 60px -20px rgba(0, 0, 0, 0.7);
}
.ipt {
  width: 100%;
  height: 33px;
  padding: 0 10px;
  border-radius:var(--radius-control);
  border: 1px solid var(--line);
  background: var(--surface-2);
  color: var(--text);
  font-size: 13.5px;
  outline: none;
}
.ipt:focus {
  border-color: var(--danger);
}
.tidy-table td:nth-child(4),.tidy-table td:nth-child(5){text-align:center;vertical-align:middle}
</style>
