<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { req, api, type Candidate, type Feed, type ScanReport } from '../api'
import { refreshAll, state, toast } from '../store'
import Modal from '../components/Modal.vue'
import Icon from '../components/Icon.vue'

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
const filter=ref(''),search=ref('')
/* 搜索与自动更新原来在列表上方各占一行：一个输入框、一个折叠面板。
   两样都是"设一次就很少动"的东西，收成图标按钮更值。 */
const searchOpen=ref(false),searchInput=ref<HTMLInputElement|null>(null),refreshOpen=ref(false)
function toggleSearch(){searchOpen.value=!searchOpen.value;if(searchOpen.value)requestAnimationFrame(()=>searchInput.value?.focus())}
function closeSearch(){searchOpen.value=false;if(search.value){search.value='';}}
const visible=computed(()=>feeds.value.filter(f=>(!filter.value||f.kind===filter.value)&&(!search.value||`${f.label} ${f.target}`.toLowerCase().includes(search.value.trim().toLowerCase()))))
/** 四张卡就是四种来源。点一下只看这一类，再点一下回到全部。 */
const METRICS=computed(()=>[
 {key:'',label:'全部订阅',count:feeds.value.length,hint:`${active.value.length} 启用 · ${idle.value.length} 停用`,tone:'metric-tone-discover'},
 {key:'topic',label:'话题',count:feeds.value.filter(f=>f.kind==='topic').length,hint:'按话题 ID 抓新动态',tone:'metric-tone-accent'},
 {key:'collection',label:'UP 合集',count:feeds.value.filter(f=>f.kind==='collection').length,hint:'按合集追更',tone:'metric-tone-editor'},
 {key:'space',label:'UP 动态',count:feeds.value.filter(f=>f.kind==='space').length,hint:'盯住指定 UP',tone:'metric-tone-warn'},
])
function pick(key:string){filter.value=filter.value===key?'':key}

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
    toast(report.value.reused && !report.value.scanned ? '共享订阅刚刚更新或正在更新，已复用本次结果' : `命中 ${report.value.matched} 条，去重后 ${report.value.unique} 个活动${report.value.problems.length?'，部分来源未完成':''}`, report.value.problems.length?'warn':'ok')
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
  <div class="sources-workspace">
    <section class="panel page-heading">
      <div><h2>订阅源</h2><span class="heading-sub">{{ active.length }} 个启用 · {{ idle.length }} 个停用 · 话题与合集所有账号共用<template v-if="filter||search"> · 筛到 {{ visible.length }} 个</template></span></div>
      <div class="toolbar">
        <button class="tool-icon" :class="{on:searchOpen||!!search}" title="搜索名称或 ID" aria-label="搜索订阅源" @click="toggleSearch"><Icon name="search" :size="16"/></button>
        <button class="auto-refresh" :class="{on:refreshEnabled}" @click="refreshOpen=true"><Icon name="refresh" :size="16"/>自动更新<span v-if="refreshEnabled"> · {{refreshMinutes}} 分钟</span></button>
        <button @click="directoryOpen=true"><Icon name="cloud" :size="16"/>公开目录</button>
        <button @click="addDialog?.showModal()"><Icon name="plus" :size="16"/>添加订阅</button>
        <button :disabled="!!busy || !visible.some(f=>f.enabled)" @click="scan(false)">{{ busy === 'scan' ? '扫描中…' : '更新当前列表' }}</button>
        <button class="primary" :disabled="!!busy || !visible.some(f=>f.enabled)" @click="scan(true)">续扫当前列表</button>
      </div>
      <div v-if="searchOpen" class="search-strip"><input ref="searchInput" v-model="search" class="ipt" placeholder="名称或 ID" aria-label="搜索订阅源" @keyup.esc="closeSearch"/><button class="mini" @click="closeSearch">清空并收起</button></div>
    </section>

    <div class="metric-row">
      <button v-for="card in METRICS" :key="card.key" class="panel metric-card" :class="[card.tone,{selected:filter===card.key}]" @click="pick(card.key)">
        <span>{{card.label}}</span><strong>{{card.count}}</strong><small>{{card.hint}}</small>
      </button>
    </div>

    <section class="panel list-panel">
      <div class="list-scroll">
        <table class="dense source-table">
          <thead><tr><th class="kind-col">类型</th><th class="cell-align-start">名称</th><th class="cell-align-start target-col">目标</th><th class="scan-col">最近扫描</th><th class="state-col">状态</th><th class="action-col">操作</th></tr></thead>
          <tbody>
            <tr v-for="feed in visible" :key="feed.id">
              <td><span class="chip" :class="'source-type-'+feed.kind">{{ labels[feed.kind] || feed.kind }}</span></td>
              <td class="cell-align-start name-cell">
                <input v-if="editing === feed.id" v-model="draft" aria-label="订阅源名称" class="ipt" maxlength="40" @keyup.enter="saveRename(feed)" @keyup.esc="editing=0" @blur="saveRename(feed)" v-focus />
                <strong v-else>{{ feed.label }}</strong>
                <small v-if="feed.error" class="source-error" role="status">{{feed.error}}</small>
              </td>
              <td class="cell-align-start target-col"><span class="kbd">{{feed.target}}</span></td>
              <td class="scan-col">{{ feed.last_scan_at ? new Date(feed.last_scan_at).toLocaleString('zh-CN') : '未扫描' }}</td>
              <td><span class="chip" :class="feed.enabled?'chip-ok':''">{{ feed.enabled ? '启用' : '已停用' }}</span></td>
              <td><div class="row-controls">
                <button class="mini" :disabled="!!busy" @click="scan(false,feed.id)">更新</button>
                <button class="mini" :disabled="!!busy" @click="scan(true,feed.id)">续扫</button>
                <details class="source-menu"><summary>管理</summary><div class="toolbar"><button class="mini" @click="startRename(feed)">改名</button><button class="mini" @click="toggle(feed)">{{ feed.enabled ? '停用' : '启用' }}</button><button class="mini danger" @click="destroy(feed)">删除</button></div></details>
              </div></td>
            </tr>
            <tr v-if="loading"><td colspan="6" class="empty-state">读取中…</td></tr>
            <tr v-else-if="!feeds.length"><td colspan="6" class="empty-state">添加话题或 UP，开始发现活动。</td></tr>
            <tr v-else-if="!visible.length"><td colspan="6" class="empty-state">没有匹配的订阅源</td></tr>
          </tbody>
        </table>
      </div>
      <footer v-if="report" class="list-footer scan-result" role="status">扫描 {{ report.scanned }} 条 · 命中 {{ report.matched }} 条 · {{ report.unique }} 个活动
        <span v-if="report.problems.length" class="warning">{{ report.problems.join('；') }}</span>
      </footer>
    </section>

    <Modal v-if="refreshOpen" title="自动更新订阅源" @close="refreshOpen=false"><div class="filter-grid">
      <div class="filter-group"><h3>开关</h3><div class="compact-options"><label><input type="checkbox" v-model="refreshEnabled"/>定时读取已启用的来源</label></div></div>
      <div class="filter-group"><h3>间隔</h3><div class="compact-options"><select class="ipt" v-model.number="refreshMinutes" aria-label="来源更新间隔" style="width:160px"><option :value="30">30 分钟</option><option :value="60">1 小时</option><option :value="180">3 小时</option><option :value="360">6 小时</option><option :value="1440">1 天</option></select></div></div>
    </div><p class="muted text-[12px]">只更新来源资料和活动线索，不会替你参与任何活动。</p><div class="modal-actions"><button @click="refreshOpen=false">取消</button><button class="primary" :disabled="refreshBusy" @click="saveRefresh();refreshOpen=false">{{refreshBusy?'保存中…':'保存'}}</button></div></Modal>
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
        <div v-for="candidate in candidates" :key="candidate.uid" class="flex items-center gap-2"><div class="min-w-0 flex-1"><strong>{{candidate.name}}</strong><p class="muted text-xs">{{candidate.fans.toLocaleString()}} 粉丝</p></div><button class="mini" @click="subscribe(candidate)">订阅动态</button></div>
        <p v-if="problems.length" class="warning">{{ problems.join('；') }}</p>
      </div></details>
    </div></dialog>
  </div>
</template>
<style scoped>
.sources-workspace{max-width:1440px;margin:auto;display:flex;flex-direction:column;gap:18px;height:100%;min-height:520px}
.sources-workspace>.list-panel{flex:1;min-height:0}
/* 自动更新开着时按钮只换描边色：它是状态提示，不是这页的主操作，
   做成实心主按钮会跟右边的「续扫当前列表」抢眼睛。 */
.auto-refresh.on{border-color:var(--accent);color:var(--accent)}
.search{width:260px;flex:none}
.refresh-settings{border-top:1px solid var(--line-soft);padding-top:10px}
.refresh-settings summary{font-size:var(--text-meta);color:var(--muted)}
.refresh-settings>div{margin-top:10px;gap:14px;font-size:var(--text-meta)}
.refresh-settings select{width:120px}
.refresh-check{display:inline-flex;align-items:center;gap:7px;cursor:pointer}
.source-table{width:100%;min-width:1040px;table-layout:fixed}
.source-table td,.source-table th{text-align:center;vertical-align:middle;white-space:normal}
.source-table tbody td{border-bottom:1px solid var(--line-soft)}
.source-table tbody tr:last-child td{border-bottom:0}
.source-table tbody tr:hover{background:var(--surface-2)}
.kind-col{width:104px}.name-cell{min-width:220px;padding-inline:16px!important}
.name-cell strong{font-size:var(--text-body);font-weight:600}
.target-col{width:260px}.target-col .kbd{overflow-wrap:anywhere;white-space:normal}
.scan-col{width:170px;font-size:var(--text-meta);color:var(--muted)}
.state-col{width:92px}.action-col{width:250px}
.source-error{display:block;margin-top:6px;font-size:var(--text-small);line-height:1.5;color:var(--warn)}
.source-menu{position:relative}
.source-menu summary{padding:0 8px;height:30px;display:inline-flex;align-items:center;color:var(--muted);font-size:var(--text-meta);border:1px solid var(--line);border-radius:var(--radius-control)}
.source-menu>div{position:absolute;right:0;top:36px;padding:10px;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-control);box-shadow:var(--shadow);z-index:3}
.scan-result{gap:12px;justify-content:flex-start;flex-wrap:wrap}
.source-type-topic{color:var(--discover);background:color-mix(in srgb,var(--discover) 10%,transparent)}
.source-type-collection{color:var(--editor);background:color-mix(in srgb,var(--editor) 10%,transparent)}
.source-type-space{color:var(--accent);background:color-mix(in srgb,var(--accent) 10%,transparent)}
.source-type-following{color:var(--warn);background:color-mix(in srgb,var(--warn) 10%,transparent)}
</style>
