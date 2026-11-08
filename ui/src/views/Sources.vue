<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { req, api, type Candidate, type Feed, type ScanReport } from '../api'
import { refreshAll, state, toast } from '../store'
import Modal from '../components/Modal.vue'

const directoryOpen=ref(false),directoryBusy=ref(false),directoryResult=ref<{read:number;added:number;updated:number;skipped:number;more:boolean}>()
async function readDirectory(restart=false){directoryBusy.value=true;try{directoryResult.value=await req('/api/public-catalog/read',{method:'POST',body:JSON.stringify({restart})});toast(`已加入 ${directoryResult.value!.added} 条活动奖品`,'ok')}catch(e){toast(String(e),'danger')}finally{directoryBusy.value=false}}

const feeds = ref<Feed[]>([])
const loading = ref(true)
const busy = ref('')
const addDialog=ref<HTMLDialogElement|null>(null)
const refreshEnabled=ref(false),refreshMinutes=ref(60),refreshBusy=ref(false)
async function loadRefresh(){try{const r=await req<{enabled:boolean;interval_min:number}>('/api/feeds/refresh');refreshEnabled.value=r.enabled;refreshMinutes.value=r.interval_min}catch(e){toast(String(e),'danger')}}
async function saveRefresh(){const owner=state.overview?.accounts.find(a=>a.current);if(!owner)return;refreshBusy.value=true;try{await req('/api/feeds/refresh',{method:'PUT',body:JSON.stringify({account_id:owner.id,enabled:refreshEnabled.value,interval_min:refreshMinutes.value})});toast('自动更新已保存','ok')}catch(e){toast(String(e),'danger');await loadRefresh()}finally{refreshBusy.value=false}}

const kind = ref<'following' | 'space' | 'topic' | 'collection'>('topic')
const labels: Record<string,string> = {following:'关注流',space:'UP 动态',topic:'话题',collection:'UP 合集'}
const target = ref('')
const label = ref('')
const report = ref<ScanReport | null>(null)

const keyword = ref('转发抽奖')
const candidates = ref<Candidate[]>([])
const problems = ref<string[]>([])

const active = computed(() => feeds.value.filter((feed) => feed.enabled))
const idle = computed(() => feeds.value.filter((feed) => !feed.enabled))
const filter=ref('topic'),search=ref('')
const visible=computed(()=>feeds.value.filter(f=>(!filter.value||f.kind===filter.value)&&(!search.value||`${f.label} ${f.target}`.toLowerCase().includes(search.value.trim().toLowerCase()))))

async function load(quiet=false) {
  if(!quiet) loading.value = true
  try {
    feeds.value = (await api.feeds()).items
  } catch (error) {
    if(!quiet) toast(error instanceof Error ? error.message : '读取订阅源失败', 'danger')
  } finally {
    loading.value = false
  }
}

async function add() {
  if (kind.value !== 'following' && !target.value.trim()) {
    toast(kind.value==='topic'?'请输入话题 ID 或链接':'请输入 UP UID 或空间链接', 'warn')
    return
  }
  busy.value = 'add'
  try {
    await api.addFeed({
      kind: kind.value,
      target: kind.value === 'following' ? '' : target.value.trim(),
      label: label.value.trim(),
    })
    target.value = ''
    label.value = ''
    toast('已加入订阅源', 'ok')
    addDialog.value?.close()
    await load()
  } catch (error) {
    toast(error instanceof Error ? error.message : '添加失败', 'danger')
  } finally {
    busy.value = ''
  }
}

async function toggle(feed: Feed) {
  try {
    await api.patchFeed(feed.id, { enabled: !feed.enabled })
    toast(feed.enabled ? `已停用「${feed.label}」` : `已启用「${feed.label}」`, 'ok')
    await load()
  } catch (error) {
    toast(error instanceof Error ? error.message : '操作失败', 'danger')
  }
}

const editing = ref(0)
const draft = ref('')

// 行内改名。进编辑就聚焦并全选 —— 否则要先把旧名字删掉才能输入。
const vFocus = {
  mounted: (el: HTMLInputElement) => {
    el.focus()
    el.select()
  },
}

function startRename(feed: Feed) {
  editing.value = feed.id
  draft.value = feed.label
}

async function saveRename(feed: Feed) {
  // 回车和失焦都会走到这里，先退出的那个负责改，后到的直接走。
  if (editing.value !== feed.id) return
  const label = draft.value.trim()
  editing.value = 0
  if (label === feed.label) return
  try {
    await api.patchFeed(feed.id, { label })
    await load()
  } catch (error) {
    toast(error instanceof Error ? error.message : '改名失败', 'danger')
  }
}

async function destroy(feed: Feed) {
  const ok = window.confirm(
    `删除订阅源「${feed.label}」？已收录活动会保留。`,
  )
  if (!ok) return
  try {
    await api.deleteFeed(feed.id)
    toast(`已删除「${feed.label}」`, 'ok')
    await load()
  } catch (error) {
    toast(error instanceof Error ? error.message : '删除失败', 'danger')
  }
}

async function scan(history=false, feedId?:number) {
  const ids=visible.value.filter(f=>f.enabled).map(f=>f.id)
  if(!feedId&&!ids.length)return
  busy.value = 'scan'
  report.value = null
  try {
    report.value = await api.scan(1, 10, history, feedId, feedId?undefined:ids)
    toast(`命中 ${report.value.matched} 条，去重后 ${report.value.unique} 个活动${report.value.problems.length?'，部分来源未完成':''}`, report.value.problems.length?'warn':'ok')
    await load()
    await refreshAll()
  } catch (error) {
    toast(error instanceof Error ? error.message : '扫描失败', 'danger')
  } finally {
    busy.value = ''
  }
}

async function discover() {
  busy.value = 'discover'
  candidates.value = []
  problems.value = []
  try {
    const result = await api.discover(keyword.value.trim(), 10, false)
    candidates.value = result.candidates
    problems.value = result.problems
    if (!result.candidates.length) toast('没有找到符合条件的 UP', 'warn')
  } catch (error) {
    toast(error instanceof Error ? error.message : '查找失败', 'danger')
  } finally {
    busy.value = ''
  }
}

async function subscribe(candidate: Candidate) {
  try {
    await api.addFeed({ kind: 'space', target: candidate.uid, label: candidate.name })
    toast(`已订阅 ${candidate.name}`, 'ok')
    await load()
  } catch (error) {
    toast(error instanceof Error ? error.message : '订阅失败', 'danger')
  }
}

let poll:ReturnType<typeof setInterval>|undefined
onMounted(()=>{void load();void loadRefresh();poll=setInterval(()=>{if(!busy.value&&!editing.value)void load(true)},30000)})
onUnmounted(()=>{if(poll)clearInterval(poll)})
</script>

<template>
  <div class="sources-layout">
    <section class="panel source-list">
      <div class="panel-head">
        <span class="dim text-sm">{{ active.length }} 个启用 · {{ idle.length }} 个停用</span>
        <div class="toolbar"><button @click="directoryOpen=true">公开目录</button><button class="primary" @click="addDialog?.showModal()">添加订阅</button><button :disabled="!!busy || !visible.some(f=>f.enabled)" @click="scan(false)">{{ busy === 'scan' ? '扫描中…' : '更新当前列表' }}</button><button :disabled="!!busy || !visible.some(f=>f.enabled)" @click="scan(true)">续扫当前列表</button></div>
      </div>
      <details class="refresh-settings"><summary>自动更新 · {{refreshEnabled?'已开启，每 '+refreshMinutes+' 分钟':'未开启'}}</summary><div class="toolbar mt-3"><label><input type="checkbox" v-model="refreshEnabled" /> 自动读取来源</label><label>间隔 <select v-model.number="refreshMinutes" aria-label="来源更新间隔"><option :value="30">30 分钟</option><option :value="60">1 小时</option><option :value="180">3 小时</option><option :value="360">6 小时</option><option :value="1440">1 天</option></select></label><button :disabled="refreshBusy" @click="saveRefresh">保存</button><span class="muted text-xs">仅更新资料，不参与活动。</span></div></details>
      <div class="source-filter toolbar"><div class="type-tabs" aria-label="订阅类型"><button :class="{'selected':filter==='topic'}" :aria-pressed="filter==='topic'" @click="filter='topic'">话题 <span>{{feeds.filter(f=>f.kind==='topic').length}}</span></button><button class="collection-tab" :class="{'selected':filter==='collection'}" :aria-pressed="filter==='collection'" @click="filter='collection'">UP 合集 <span>{{feeds.filter(f=>f.kind==='collection').length}}</span></button></div><input class="ipt min-w-0 flex-1" v-model="search" aria-label="搜索订阅源" placeholder="名称或 ID" /><button class="plain-filter" :aria-pressed="filter===''" @click="filter=''">全部</button></div>
      <div v-if="loading" class="empty-state">读取中…</div>
      <div v-else-if="!feeds.length" class="empty-state">添加话题或 UP，开始发现活动。</div>
      <div v-if="!loading && feeds.length && !visible.length" class="empty-state">没有匹配的订阅源</div>
      <div v-for="feed in visible" :key="feed.id" class="source-row row-hover" :class="'source-row-'+feed.kind">
        <div class="source-main">
          <span class="chip" :class="'source-type-'+feed.kind">{{ labels[feed.kind] || feed.kind }}</span>
          <div class="min-w-0">
            <input v-if="editing === feed.id" v-model="draft" aria-label="订阅源名称" class="ipt" maxlength="40" @keyup.enter="saveRename(feed)" @keyup.esc="editing=0" @blur="saveRename(feed)" v-focus />
            <strong v-else class="block truncate">{{ feed.label }}</strong>
            <div class="muted text-xs mt-1">{{feed.target}} · {{ feed.last_scan_at ? new Date(feed.last_scan_at).toLocaleString('zh-CN') : '未扫描' }} <span v-if="!feed.enabled"> · 已停用</span></div>
            <p v-if="feed.error" class="source-error" role="status">{{feed.error}}</p>
          </div>
        </div>
        <div class="toolbar source-ops">
          <button :disabled="!!busy" @click="scan(false,feed.id)">更新</button>
          <button :disabled="!!busy" @click="scan(true,feed.id)">续扫</button>
          <details class="source-menu"><summary>管理</summary><div class="toolbar"><button @click="startRename(feed)">改名</button>
          <button @click="toggle(feed)">{{ feed.enabled ? '停用' : '启用' }}</button>
          <button class="danger" @click="destroy(feed)">删除</button></div></details>
        </div>
      </div>
      <div v-if="report" class="scan-result" role="status">扫描 {{ report.scanned }} 条 · 命中 {{ report.matched }} 条 · {{ report.unique }} 个活动
        <p v-if="report.problems.length" class="warning mt-2">{{ report.problems.join('；') }}</p>
      </div>
    </section>
    <Modal v-if="directoryOpen" title="公开活动目录" :can-close="()=>!directoryBusy" @close="directoryOpen=false"><div class="space-y-4">
      <p class="muted">只读导入奖品资料，不上传账号或参与记录。</p>
      <p v-if="directoryResult" role="status">读取 {{directoryResult.read}} 条 · 新增 {{directoryResult.added}} 条 · 修正 {{directoryResult.updated||0}} 条 · 跳过已有、过期或屏蔽项 {{directoryResult.skipped}} 条</p>
      <p v-if="directoryResult&&!directoryResult.more" class="muted">已到目录末尾</p>
      <div class="toolbar"><button class="primary" :disabled="directoryBusy" @click="readDirectory(false)">{{directoryBusy?'读取中…':directoryResult?.more?'读取下一批':'读取目录'}}</button><button :disabled="directoryBusy" @click="readDirectory(true)">从头核对</button><RouterLink to="/campaigns" @click="directoryOpen=false">查看奖品 ↗</RouterLink></div>
    </div></Modal>
    <dialog ref="addDialog" class="modal"><header><h2>添加订阅</h2><button @click="addDialog?.close()">关闭</button></header><div class="modal-body space-y-4">
      <section class="space-y-4">
        <label class="field">类型<select class="ipt" v-model="kind"><option value="topic">话题</option><option value="collection">UP 合集</option><option value="space">UP 动态</option><option value="following">关注流</option></select></label>
        <label v-if="kind !== 'following'" class="field">{{kind==='topic'?'话题 ID / 链接':'UP UID / 空间链接'}}<input class="ipt" v-model="target" @keyup.enter="add" :placeholder="kind==='topic'?'https://www.bilibili.com/v/topic/detail?topic_id=…':'https://space.bilibili.com/…'" /></label>
        
        <label class="field">备注<input class="ipt" v-model="label" placeholder="可选" maxlength="40" @keyup.enter="add" /></label>
        <button class="primary w-full" :disabled="!!busy" @click="add">{{busy==='add'?'添加中…':'添加'}}</button>
      </section>
      <details><summary>查找 UP</summary><div class="space-y-3 mt-3">
        <label class="field">昵称或关键词<input class="ipt" v-model="keyword" @keyup.enter="discover" /></label>
        <button :disabled="!!busy" @click="discover">查找</button>
        <div v-for="candidate in candidates" :key="candidate.uid" class="flex items-center gap-2"><div class="min-w-0 flex-1"><strong>{{candidate.name}}</strong><p class="muted text-xs">{{candidate.fans.toLocaleString()}} 粉丝</p></div><button @click="subscribe(candidate)">订阅动态</button></div>
        <p v-if="problems.length" class="warning">{{ problems.join('；') }}</p>
      </div></details>
    </div></dialog>
  </div>
</template>
<style scoped>
.sources-layout{display:block}
.refresh-settings{padding:12px 18px;border-bottom:1px solid var(--line-soft)}
.source-main{display:flex;align-items:center;gap:14px;min-width:0;flex:1}
.source-row{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:16px 18px;border-bottom:1px solid var(--line-soft)}
.source-ops{flex:none;gap:6px}
.source-ops button{font-size:var(--text-meta)}
.source-filter{padding:12px 18px;border-bottom:1px solid var(--line-soft)}
.source-filter select{width:130px}
.source-menu{position:relative}
.source-menu summary{padding:6px 9px;color:var(--muted)}
.source-menu>div{position:absolute;right:0;top:35px;padding:12px;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-control);box-shadow:var(--shadow);z-index:3;width:220px}
.source-error{font-size:var(--text-meta);line-height:1.5;color:var(--warn);margin-top:6px;max-width:650px}
.scan-result{padding:16px 18px;font-size:var(--text-control)}
.source-type-topic{color:var(--discover);background:color-mix(in srgb,var(--discover) 10%,transparent)}
.source-type-collection{color:var(--editor);background:color-mix(in srgb,var(--editor) 10%,transparent)}
.source-type-space{color:var(--accent)}
.source-type-following{color:var(--warn)}
@media(max-width:800px){.source-row{flex-wrap:wrap}.source-main{flex-basis:100%}.panel-head{flex-wrap:wrap}}
</style>
