<script setup lang="ts">
import { computed,onMounted,onUnmounted,ref } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { registerWorkGuard } from '../workGuard'
import { req } from '../api'
import Countdown from '../components/Countdown.vue'
import { state,toast,refreshQueue } from '../store'
const props=defineProps<{embedded?:boolean}>()
const emit=defineEmits<{navigate:[]}>()
const router=useRouter()
function navigate(key:Key){if(props.embedded&&!mayLeave())return;emit('navigate');void router.push(choices[key].link)}
type Key='topics'|'collections'|'watch'|'results'|'messages'|'participate'|'cleanup'
type Period={key:Key;enabled:boolean;minutes:number}
type Run={at:string;ok:boolean;scanned:number;written:number;note:string}
type Plan={next_scheduled?:{label:string;at:number};account_id:number;running:boolean;apply:boolean;selection:'queue'|'catalog';limit:number;hourly_limit:number;hourly_used:number;active:{key?:Key;since?:string;note?:string};tasks:Period[];legacy_running:boolean;sources_running:boolean;states:Record<Key,{next_at:string;history:Run[]}>}
const choices:Record<Key,{name:string;note:string;tone:string;link:string}>={
 topics:{name:'话题更新',note:'读取已订阅话题中的新活动',tone:'blue',link:'/sources'},
 collections:{name:'UP 合集更新',note:'读取专栏活动链接，继续未完成的扫描',tone:'purple',link:'/sources'},
 watch:{name:'UP 动态更新',note:'更新 UP 动态和关注流订阅',tone:'blue',link:'/sources'},
 results:{name:'开奖核验',note:'核对已参与活动，保存开奖结论',tone:'amber',link:'/results'},
 messages:{name:'中奖消息',note:'读取私信、回复和 @，不标记为已看',tone:'purple',link:'/inbox'},
 participate:{name:'参与活动',note:'按所选范围和每轮上限执行',tone:'teal',link:'/pool'},
 cleanup:{name:'清理检查',note:'检查可清理项目，实际删除仍需逐条确认',tone:'amber',link:'/tidy'},
}
const view=ref<Plan|null>(null),form=ref<Plan|null>(null),busy=ref(''),dirty=ref(false),error=ref('')
const account=computed(()=>state.overview?.accounts.find(x=>x.current))
const running=computed(()=>view.value?.running||view.value?.legacy_running)
const enabled=computed(()=>form.value?.tasks.filter(t=>t.enabled).length||0)
const histories=computed(()=>Object.entries(view.value?.states||{}).flatMap(([key,s])=>s.history.map(r=>({...r,key:key as Key}))).sort((a,b)=>b.at.localeCompare(a.at)).slice(0,20))
let poll:ReturnType<typeof setInterval>|undefined
async function load(initial=false){try{const data=await req<Plan>('/api/tasks');view.value=data;if(initial||!form.value||(!dirty.value&&!busy.value)){form.value=structuredClone(data);if(!data.apply&&data.running){const p=form.value.tasks.find(t=>t.key==='participate');if(p)p.enabled=false}dirty.value=false}error.value=''}catch(e){error.value=String(e)}}
function changed(){dirty.value=true}
function preset(mode:'read'|'queue'|'catalog'){
 if(!form.value)return
 form.value.selection=mode==='catalog'?'catalog':'queue';form.value.apply=false
 for(const t of form.value.tasks)t.enabled=mode==='read'?t.key!=='participate'&&t.key!=='watch':mode==='queue'?t.key==='participate':['topics','collections','results','messages','participate','cleanup'].includes(t.key)
 dirty.value=true
}
async function save(start:boolean){if(!form.value||!account.value||busy.value)return;const f=form.value;f.apply=f.tasks.some(t=>t.key==='participate'&&t.enabled);const writes=start&&f.apply
 busy.value='save';try{view.value=await req<Plan>('/api/tasks',{method:'PUT',body:JSON.stringify({...f,account_id:account.value.id,running:start,confirmed:writes})});form.value=JSON.parse(JSON.stringify(view.value));dirty.value=false;toast(start?'任务设置已生效':'设置已保存','ok')}catch(e){toast(String(e),'danger')}finally{busy.value=''}}
async function stop(){if(!account.value)return;busy.value='stop';try{view.value=await req<Plan>(`/api/tasks?account_id=${account.value.id}`,{method:'DELETE'});toast('已停止排期；正在处理的动作完成后结束','ok');await load(true)}catch(e){toast(String(e),'danger')}finally{busy.value=''}}
async function once(key:Key){if(!account.value||!form.value||busy.value)return;const apply=key==='participate';
 busy.value=key;try{const r=await req<Run>('/api/tasks/once',{method:'POST',body:JSON.stringify({account_id:account.value.id,key,selection:form.value.selection,limit:form.value.limit,hourly_limit:form.value.hourly_limit,apply,confirmed:apply})});toast(r.note,r.ok?'ok':'warn');await load();void refreshQueue()}catch(e){toast(String(e),'danger')}finally{busy.value=''}}
function mayLeave(){return !dirty.value||window.confirm('定时任务设置尚未保存，确定离开？')}
const removeGuard=registerWorkGuard(mayLeave)
if(!props.embedded)onBeforeRouteLeave(mayLeave)
defineExpose({mayLeave})
onMounted(()=>{void load(true);poll=setInterval(()=>void load(),3000)})
onUnmounted(()=>{removeGuard();if(poll)clearInterval(poll)})
</script>
<template>
 <div class="task-workspace" :class="{embedded:embedded}">
  <section class="panel task-heading"><div><h2>定时任务 <span class="chip" :class="running?'chip-ok':''">{{running?'运行中':'已停止'}}</span></h2><p class="dim">{{account?.nickname}} · 已选 {{enabled}} 项功能<span v-if="dirty" class="warning"> · 修改后请保存生效</span></p></div><div class="toolbar"><button :disabled="!!busy" @click="save(!!view?.running)">保存设置</button><button v-if="running" class="danger" :disabled="!!busy" @click="stop">停止全部任务</button><button v-else class="primary" :disabled="!!busy||!enabled" @click="save(true)">启动所选任务</button></div></section>
  <div v-if="view?.next_scheduled" class="task-countdown">下次：{{view.next_scheduled.label}}<Countdown :at="view.next_scheduled.at"/></div>
  <p v-if="view?.active.key" class="task-notice" role="status">正在执行 {{choices[view.active.key].name}} · {{view.active.note}}<span v-if="!running"> · 本条完成后停止</span></p>
  <p v-if="error" class="warning" role="alert">{{error}}</p>
  <p v-if="view?.legacy_running" class="task-notice">原参与排期仍在运行，停止后可使用新的功能组合。</p>
  <p v-else-if="view?.sources_running&&!running" class="task-notice">订阅源的自动更新仍在运行。启动这里的来源任务后会接管更新，避免重复扫描。</p>
  <details class="panel task-presets"><summary>快捷配置</summary><div class="toolbar"><button :disabled="!!busy" @click="preset('read')">日常更新与检查</button><button :disabled="!!busy" @click="preset('queue')">仅计划单参与</button><button :disabled="!!busy" @click="preset('catalog')">奖品库自动参与</button></div></details>
  <div v-if="form" class="task-grid">
   <section v-for="task in form.tasks" :key="task.key" class="panel task-card" :class="['tone-'+choices[task.key].tone,{'task-selected':task.enabled,'participation-card':task.key==='participate'}]">
    <div class="task-card-head"><label><input type="checkbox" v-model="task.enabled" :disabled="!!busy" @change="changed"/><strong>{{choices[task.key].name}}</strong></label><button class="task-link" @click="navigate(task.key)">查看</button></div>
    
    <div class="task-period"><label>每 <input class="ipt" type="number" :min="['topics','collections','watch'].includes(task.key)?30:5" max="10080" v-model.number="task.minutes" :disabled="!!busy" @input="changed"/> 分钟</label><button :disabled="!!busy" @click="once(task.key)">{{busy===task.key?'处理中…':task.key==='participate'?'参与一次':'运行一次'}}</button></div>
    <div v-if="task.key==='participate'" class="participation-options"><label class="field">活动奖品范围<select class="ipt" v-model="form.selection" :disabled="!!busy" @change="changed"><option value="queue">仅待参与</option><option value="catalog">待参与优先，再选奖品库</option></select></label><label class="field">每轮上限<input class="ipt" type="number" min="1" max="20" v-model.number="form.limit" :disabled="!!busy" @input="changed"/></label><label class="field">每小时参与上限<input class="ipt" type="number" min="1" max="500" v-model.number="form.hourly_limit" :disabled="!!busy" @input="changed"/></label><div class="field"><span>过去 1 小时已尝试 {{view?.hourly_used||0}} 条</span></div></div>
    <div class="task-status"><span>下次：<Countdown v-if="view?.running&&view.tasks.some(t=>t.key===task.key&&t.enabled)&&Number(view.states[task.key]?.next_at)>0&&view.active.key!==task.key" :at="Number(view.states[task.key].next_at)"/><template v-else>{{view?.active.key===task.key?'执行中':task.enabled?'未排期':'未启用'}}</template></span><p v-if="view?.states[task.key]?.history[0]" :class="{'warning':!view.states[task.key].history[0]?.ok}">{{view.states[task.key].history[0]?.note}}</p></div>
   </section>
  </div>
  <details class="panel task-history"><summary>最近运行 · {{histories.length}} 条</summary><div class="table-scroll"><table class="dense w-full" v-if="histories.length"><thead><tr><th>时间</th><th>功能</th><th>结果</th><th>说明</th></tr></thead><tbody><tr v-for="(run,i) in histories" :key="i"><td>{{new Date(run.at).toLocaleString('zh-CN')}}</td><td>{{choices[run.key].name}}</td><td><span class="chip" :class="run.ok?'chip-ok':'chip-warn'">{{run.ok?'完成':'未完成'}}</span></td><td class="run-note">{{run.note}}</td></tr></tbody></table><p v-else class="empty-state">暂无运行记录</p></div></details>
 </div>
</template>
<style scoped>
.task-countdown{display:flex;align-items:center;gap:16px;padding:12px 20px;background:var(--discover-soft);border:1px solid var(--line);border-radius:var(--radius-control);color:var(--discover)}

.embedded .task-heading{position:static;padding:0 0 14px;background:transparent;border:0}.embedded .task-heading h2{display:none}.embedded .task-grid{gap:14px}.embedded .task-card{padding:16px}.embedded .task-period{margin-top:16px}.task-workspace.embedded .participation-card .participation-options{grid-template-columns:repeat(2,minmax(0,1fr))}.embedded .task-status{margin-top:12px;padding-top:10px}
.task-workspace{display:flex;flex-direction:column;gap:20px;padding-bottom:20px}
.task-heading{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:20px 24px;flex-wrap:wrap;position:sticky;top:0;z-index:5}
.task-heading h2{display:flex;gap:12px;align-items:center;margin-bottom:8px}
.task-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;align-items:start}
.task-card{--tone:var(--discover);padding:22px;border-top:3px solid color-mix(in srgb,var(--tone) 45%,var(--line));background:color-mix(in srgb,var(--tone) 4%,var(--surface))}
.tone-teal{--tone:var(--accent)}.tone-amber{--tone:var(--warn)}.tone-purple{--tone:var(--editor)}
.participation-card{order:-1;grid-column:1/-1}.participation-card .participation-options{grid-template-columns:repeat(4,minmax(0,1fr))}.participation-card .task-note{margin-bottom:12px}
.task-selected{border-top-color:var(--tone)}
.task-card-head{display:flex;align-items:center;justify-content:space-between;gap:12px}
.task-card-head label{display:flex;gap:12px;align-items:center;font-size:16px}
.task-card-head .task-link{border:0;background:transparent;padding:0;min-height:24px;font-size:var(--text-meta);color:var(--tone);text-decoration:none}
.task-note{font-size:var(--text-control);margin:12px 0 20px;line-height:1.7}
.task-period{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.task-period input{width:90px;margin:0 8px}.task-period label{font-size:var(--text-control)}
.task-status{font-size:var(--text-meta);line-height:1.7;color:var(--muted);border-top:1px solid var(--line);padding-top:16px;margin-top:20px}
.task-status p{margin-top:8px;overflow-wrap:anywhere}
.participation-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;padding-top:20px}
.write-mode{grid-column:1/-1;display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:var(--text-control)}
.write-mode .muted{font-size:var(--text-meta)}.task-history summary{padding:18px 22px}
.table-scroll{overflow:auto}.task-history table{min-width:650px}.task-history th:nth-child(1){width:180px}.task-history th:nth-child(2){width:120px}.task-history th:nth-child(3){width:90px}.run-note{white-space:normal}
.task-presets summary{padding:16px 22px}.task-presets .toolbar{padding:0 22px 18px}
.task-notice{padding:14px 20px;background:var(--accent-soft);border-left:3px solid var(--accent);font-size:var(--text-control)}
@media(max-width:1000px){.participation-card .participation-options{grid-template-columns:repeat(2,minmax(0,1fr))}.task-grid{grid-template-columns:1fr}}
</style>
