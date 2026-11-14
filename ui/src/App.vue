<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { extraNav } from './extensions'
import { useRoute } from 'vue-router'

import Modal from './components/Modal.vue'
import TaskLog from './components/TaskLog.vue'
import Icon from './components/Icon.vue'
import Countdown from './components/Countdown.vue'
const scheduledNext=ref<{label:string;at:number}>()
const navIcon=(path:string)=>({'/standby':'clock','/':'home','/campaigns':'compass','/pool':'queue','/sources':'source','/shelf':'archive','/cleaned':'trash','/results':'gift','/inbox':'inbox','/tidy':'trash','/digest':'pen','/settings':'settings','/blocks':'filter','/exchange':'cloud','/activity':'clock'} as Record<string,string>)[path]||'pen'
import Auto from './views/Auto.vue'
import LoginPanel from './components/LoginPanel.vue'
import AccountMenu from './components/AccountMenu.vue'
import Toasts from './components/Toasts.vue'
import {
  applyTheme,
  dismiss,
  refreshAll,
  refreshGuard,
  refreshQueue,
  checkAccountSessions,
  startEvents,
  startInboxWatch,
  state,
  stopInboxWatch,
} from './store'
import { req } from './api'
import { unlock } from './sound'

const route = useRoute()
const appVersion = __APP_VERSION__
const tasksOpen=ref(false)
const resultAttention=ref(0)
const messageAttention=computed(()=>Math.max(state.unreadMessages||0,Math.max(0,(state.gate?.unread_chat||0)-(state.gate?.acked_chat||0))+Math.max(0,(state.gate?.unread_system||0)-(state.gate?.acked_system||0))))
function badge(path:string){return path==='/campaigns'?(state.queueCount||0):path==='/results'?resultAttention.value:path==='/standby'?messageAttention.value:0}
function badgeTitle(path:string){
  if(path==='/campaigns')return `${state.queueCount||0} 条待参与`
  if(path==='/standby')return messageAttention.value?`${messageAttention.value} 条消息未看 · 当前账号`:'挂机运作'
  return resultAttention.value?`${resultAttention.value} 条需要人看一眼（待核对 / 缺 UP 信息）`:'获奖校验'
}
async function refreshAttention(){const owner=accountKey.value;try{const r=await req<{account_id:number;results:number}>('/api/history/attention');if(owner===accountKey.value&&r.account_id===owner)resultAttention.value=r.results}catch{}}

const taskPanel=ref<InstanceType<typeof Auto>>()
const closeTasks=()=>taskPanel.value?.mayLeave()??true
const groups = computed(() => [
  {label:'运营中心',color:'var(--text-dim)',items:[{to:'/',label:'运营中心',hidden:false}]},
  /* 消息不再单独占一个页签：它是"哪个账号有新消息"，属于挂机运作的上下文。
     仍然留在 items 里（hidden），这样停在 /inbox 时侧栏高亮还是「挂机运作」，
     但页签条里不会多出一个入口。 */
  {label:'挂机运作',color:'var(--accent)',items:[{to:'/standby',label:'挂机运作',hidden:false},{to:'/inbox',label:'消息',hidden:true}]},
  {label:'活动中心',color:'var(--discover)',items:[{to:'/campaigns',label:'发现奖品',hidden:false},{to:'/pool',label:'待参与',hidden:false},{to:'/sources',label:'订阅源',hidden:false},{to:'/shelf',label:'往期架',hidden:false},{to:'/cleaned',label:'已清理',hidden:false}]},
  {label:'获奖校验',color:'var(--warn)',items:[{to:'/results',label:'开奖核验',hidden:false},{to:'/tidy',label:'清理',hidden:false}]},
  ...extraNav.filter(x=>x.to!='/exchange').map(x=>({label:x.label,color:'var(--editor)',items:[{...x,hidden:false}]})),
  {label:'管理设置',color:'var(--muted)',items:[{to:'/settings',label:'设置',hidden:false},...extraNav.filter(x=>x.to==='/exchange').map(x=>({...x,hidden:false})),{to:'/activity',label:'运行记录',hidden:false}]},
])
const section=computed(()=>groups.value.find(g=>g.items.some(x=>x.to===route.path)) || groups.value[0]!)
const dragging = ref(false)
const taskProgress=ref(''),nextTask=ref('')
const accountEvents=computed(()=>state.events.filter(e=>e.scope==='shared'||!e.account_id||e.account_id===accountKey.value))
let taskPoll:ReturnType<typeof setInterval>|undefined
async function refreshTask(){
 const owner=accountKey.value
 if(!state.signedIn){taskProgress.value='';scheduledNext.value=undefined;return}
 try{const data=await req<{account_id:number;active:{key?:string;note?:string};next?:{label:string;at:number};next_scheduled?:{label:string;at:number}}>('/api/tasks');if(owner===accountKey.value&&data.account_id===owner){scheduledNext.value=data.next_scheduled;taskProgress.value=data.active.note||'';nextTask.value=data.next?`${data.next.label} · ${data.next.at*1000<=Date.now()?'即将开始':new Date(data.next.at*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}`:'暂无排期'}}catch{if(owner===accountKey.value){taskProgress.value='';scheduledNext.value=undefined}}
}
let timer: number | undefined
let stopEvents: (() => void) | null = null

onMounted(async () => {
  applyTheme(state.theme)
  await refreshAll()
  void checkAccountSessions()
  stopEvents = startEvents()
  startInboxWatch()
  void refreshTask();void refreshAttention();taskPoll=setInterval(()=>{void refreshTask();void refreshQueue()},5000)
  // 浏览器不许没交互过的页面出声。第一次点哪儿都算 —— 在这里挂一次就够，
  // 之后 play() 才能真的响。
  document.addEventListener('pointerdown', unlock, { once: true })
  document.addEventListener('keydown', unlock, { once: true })
  // 闸门依赖网络，别让顶栏长时间显示过期状态。
  timer = window.setInterval(() => {void refreshGuard();void refreshAttention();void checkAccountSessions()}, 30000)
})

onUnmounted(() => {
  if (taskPoll) clearInterval(taskPoll)
  if (timer) window.clearInterval(timer)
  stopEvents?.()
  stopInboxWatch()
})

// 登录成功后，扫码组件自己会调 refreshAll，这里不再重复拉一遍 ——
// 之前加了一个 watch(signedIn)，结果是首屏每次都会发两遍 gate/quota。

function toggleTheme() {
  applyTheme(state.theme === 'dark' ? 'light' : 'dark')
}

const gateChip = computed(() => {
  if (!state.signedIn) return { cls: 'chip', text: '未登录', title: '' }
  const gate = state.gate
  if (!gate)
    return {
      cls: 'chip chip-warn',
      text: '清理状态未知',
      title: state.guardError || '读取闸门状态失败，按不可执行处理',
    }
  if (gate.paused) return { cls: 'chip chip-danger', text: '已暂停', title: gate.reason || '手动暂停' }
  if (!gate.open) return { cls: 'chip chip-danger', text: '清理暂停', title: gate.reason }
  return { cls: 'chip chip-ok', text: '清理可用', title: gate.reason }
})

const quotaText = computed(() => {
  const quota = state.quota
  if (!quota) return ''
  return quota.budget.per_hour ? `${quota.last_hour}/${quota.budget.per_hour} 次 · 1 小时` : `近 1 小时 ${quota.last_hour} 次`
})

// 头像走 B 站的 CDN，可能因为防盗链或网络拿不到。拿不到就退回昵称首字 ——
// 留一个破图标比留一个字母更难看。（头像本身的渲染在 AccountMenu 里。）
//
// 换号之后每一页的数据都换了一套，所以拿账号 id 当 key 强制重建当前页 ——
// 只刷状态不重建的话，页面里那些"进来时拉一次"的局部 ref 还是上一个号的。
const accountKey = computed(
  () => state.overview?.accounts.find((row) => row.current)?.id ?? state.overview?.account?.uid ?? 'anon',
)
watch(accountKey,()=>{resultAttention.value=0;void refreshAttention();taskProgress.value='';scheduledNext.value=undefined;void refreshTask();void refreshQueue()})
</script>

<template>
  <!-- 未登录：整屏换成扫码页。功能全要凭据，留着空壳子反而让人乱点。 -->
  <LoginPanel v-if="state.ready && !state.signedIn" />

  <div v-else class="app-shell flex h-[100dvh] overflow-hidden" :class="dragging ? 'select-none' : ''">
        <aside
          class="app-sidebar w-[192px] flex-none flex flex-col border-r"
          style="border-color: var(--line)"
        >
          <div class="flex items-center gap-2.5 px-5 h-[58px]">
            <img src="/icon.png" alt="toBili" class="w-[30px] h-[30px] flex-none" />
            <div class="leading-none">
              <div class="text-[16px] font-semibold tracking-tight">{{extraNav.length ? "toBili Pro" : "toBili"}}</div>
              <div class="text-[11.5px] muted mt-1">抽奖活动管理</div>
            </div>
          </div>

          <nav class="px-2.5 mt-3 flex-1 space-y-1" aria-label="主导航">
            <RouterLink v-for="group in groups" :key="group.label" :to="group.items[0]!.to"
              class="nav-item" :class="{'nav-active':section===group}" :style="{'--section-color':group.color}"
              :aria-current="section===group?'page':undefined">
              <Icon :name="navIcon(group.items[0]!.to)"/><span>{{group.label}}</span><span v-if="badge(group.items[0]!.to)" class="nav-badge" :title="badgeTitle(group.items[0]!.to)" :aria-label="badgeTitle(group.items[0]!.to)">{{badge(group.items[0]!.to)>99?'99+':badge(group.items[0]!.to)}}</span>
            </RouterLink>
          </nav>

          <div class="px-3 pb-3">
            <button class="theme-btn" @click="toggleTheme">
              <svg v-if="state.theme === 'dark'" width="14" height="14" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="1.8" />
                <path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
              </svg>
              <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none">
                <path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
              </svg>
              <span>{{ state.theme === 'dark' ? '浅色' : '深色' }}</span>
            </button>
            <div class="px-2 mt-2.5 text-[11.5px] muted flex items-center justify-between">
              <span :title="'当前程序版本 ' + appVersion">v{{appVersion}}</span>
              <span :title="state.connected ? '事件流已连接' : '事件流断开，正在重连'">
                <span class="inline-block w-1.5 h-1.5 rounded-full mr-1" :style="{ background: state.connected ? 'var(--ok)' : 'var(--muted)' }" />
                {{ state.connected ? '已连接' : '离线' }}
              </span>
            </div>
          </div>
        </aside>

        <main class="flex-1 min-w-0 flex flex-col">
          <header
            class="app-header h-[58px] flex-none flex items-center justify-between px-6 border-b"
            style="border-color: var(--line); background: color-mix(in srgb, var(--surface) 70%, transparent)"
          >
            <h1 class="text-[16px] font-semibold tracking-tight">{{ section.label }}</h1>

            <div class="flex items-center gap-2">
              <span :class="gateChip.cls" :title="gateChip.title">
                <span class="dot" />
                {{ gateChip.text }}
              </span>
              <span v-if="quotaText" class="chip" :title="state.quota?.explain">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
                  <path d="M13 3 5 14h6l-1 7 8-11h-6l1-7Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
                </svg>
                {{ quotaText }}
              </span>
              <div class="w-px h-5 mx-1" style="background: var(--line)" />
              <button class="task-launch" :title="scheduledNext?'下次：'+scheduledNext.label:'定时任务'" @click="tasksOpen=true"><Icon name="clock" :size="16"/>定时任务<Countdown v-if="scheduledNext" :at="scheduledNext.at"/></button><AccountMenu />
            </div>
          </header>

          <nav v-if="section.items.filter(t=>!t.hidden).length>1" class="workspace-tabs" :style="{'--section-color':section.color}" aria-label="页内导航">
            <RouterLink v-for="tab in section.items.filter(t=>!t.hidden)" :key="tab.to" :to="tab.to" :class="{'selected':route.path===tab.to,'risk-tab':tab.to==='/tidy'}" :aria-current="route.path===tab.to?'page':undefined"><Icon :name="navIcon(tab.to)" :size="16"/><span>{{tab.label}}</span><span v-if="tab.to==='/inbox'&&messageAttention" class="nav-badge">{{messageAttention}}</span><span v-if="tab.to==='/pool'" class="tab-count" aria-label="待参与条数">{{state.queueCount??'—'}}</span></RouterLink>
          </nav>
          <div class="app-content flex-1 overflow-y-auto px-5 py-4">
            <RouterView v-slot="{ Component }">
              <component :is="Component" :key="accountKey" class="enter" @refresh="refreshAll" />
            </RouterView>
          </div>
          <details class="event-ribbon">
            <summary><span class="log-label">任务日志</span> · {{ taskProgress || (accountEvents.length ? accountEvents[0]?.text : '等待新事件') }}<RouterLink class="log-problems" to="/activity?issues=1" @click.stop>异常记录</RouterLink><span class="next-task">下一项：{{nextTask}}</span></summary>
            <TaskLog compact :key="accountKey"/><RouterLink class="log-full-link" to="/activity">打开完整日志 ↗</RouterLink>
          </details>
        </main>
      </div>

      <Modal v-if="tasksOpen" title="定时任务" :can-close="closeTasks" @close="tasksOpen=false"><Auto ref="taskPanel" embedded :key="accountKey" @navigate="tasksOpen=false"/></Modal>
      <Toasts @dismiss="dismiss" />
</template>

<style scoped>
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 36px;
  padding: 0 11px;
  margin-bottom: 2px;
  border-radius:var(--radius-panel);
  font-size:var(--text-control);
  color: var(--text-dim);
  text-decoration: none;
  transition: background 0.14s ease, color 0.14s ease;
}
.nav-item:hover {
  background: var(--surface-2);
  color: var(--text);
}
.nav-active {
  background: color-mix(in srgb, var(--section-color) 12%, transparent);
  color: var(--section-color);
  font-weight: 600;
}
.theme-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  height: 30px;
  border-radius:var(--radius-control);
  border: 1px solid var(--line);
  background: transparent;
  color: var(--muted);
  font-size:var(--text-meta);
  cursor: pointer;
  transition: all 0.14s ease;
}
.theme-btn:hover {
  color: var(--text);
  background: var(--surface-2);
}
.section-mark{width:4px;height:18px;border-radius:2px;background:var(--section-color);flex:none}
.task-launch{display:inline-flex;align-items:center;gap:7px}
</style>
