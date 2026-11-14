<script setup lang="ts">
import {ref,computed,watch,onMounted,onUnmounted} from 'vue'
import {req,post} from '../api'
import {state,describeEvent,isIssue,currentAccount,toast} from '../store'
import Icon from './Icon.vue'
import Modal from './Modal.vue'
const props=defineProps<{compact?:boolean;issues?:boolean}>()
const category=ref(props.issues?'issues':''),loading=ref(false),error=ref(''),hasMore=ref(true)
type Log={seq:number;topic:string;at:string;label?:string;payload:Record<string,unknown>}
type Entry=Log&{text:string;issue:boolean;label:string}
const rows=ref<Log[]>([]),detail=ref<Entry|undefined>()
const categories=[['','全部'],['sources','话题 / 合集'],['participation','参与'],['results','开奖 / 清理'],['model','识别'],['issues','异常']]
// 徽章只放两三个字：日志是用来扫的，标签太长会把正文挤掉。
const KIND_LABEL:Record<string,string>={
 probe:'检查','participation.issue':'参与',settle:'核验',write:'操作',llm:'识别',
 'exchange.review':'识别',scan:'订阅','source.update':'订阅','tidy.run':'清理',
 'tidy.blocked':'清理','result.discard':'移除','notify.sent':'通知','notify.failed':'通知',
 'daemon.round_done':'定时','daemon.round_failed':'定时','verdict.override':'改判',
 'account.switched':'账号','account.removed':'账号','enroll':'参与','settled':'提交',
}
let generation=0,timer:ReturnType<typeof setInterval>|undefined
async function load(more=false,reset=false){
 const ticket=++generation, owner=currentAccount();loading.value=true
 try{
  const before=more?(rows.value.at(-1)?.seq||0):0
  const r=await req<{items:Log[]}>(`/api/history?limit=${props.compact?30:100}&category=${category.value}&issues_only=${category.value==='issues'}&before_seq=${before}`)
  if(ticket!==generation||owner!==currentAccount())return
  const limit=props.compact?30:100
  if(more||reset||rows.value.length<=limit)hasMore.value=r.items.length===limit
  rows.value=more?[...rows.value,...r.items]:!reset&&rows.value.length>limit?[...new Map([...r.items,...rows.value].map(x=>[x.seq,x])).values()].sort((a,b)=>b.seq-a.seq):r.items;error.value=''
 }catch(e){if(ticket===generation)error.value=String(e)}finally{if(ticket===generation)loading.value=false}
}
const entries=computed<Entry[]>(()=>rows.value.map(r=>({...r,text:describeEvent(r.topic,r.payload),issue:isIssue(r.topic,r.payload),
 // 服务端会给出定时任务的具体名字（话题/参与/核验/清理）。以前一律写"任务"，
 // 于是"话题更新"和"参与活动"在同一行里看起来是同一类事。
 label:r.label||KIND_LABEL[r.topic]||'任务'})))
async function ignoreAll(){
 const seqs=entries.value.filter(r=>r.issue).map(r=>r.seq)
 if(!seqs.length)return
 try{
  await post('/api/history/dismiss/bulk',{seqs})
  const gone=new Set(seqs);rows.value=rows.value.filter(r=>!gone.has(r.seq))
  toast(`已忽略 ${seqs.length} 条异常`,'ok')
 }catch(e){toast(String(e),'danger')}
}
const detailFields=computed(()=>{if(!detail.value)return [];const skip=new Set(['account_id','scope'])
 return Object.entries(detail.value.payload).filter(([k,v])=>!skip.has(k)&&v!==''&&v!=null&&!(Array.isArray(v)&&!v.length)).map(([k,v])=>[k,typeof v==='object'?JSON.stringify(v):String(v)] as [string,string])})
async function ignore(row:Entry){
 try{await post('/api/history/dismiss',{seq:row.seq});rows.value=rows.value.filter(r=>r.seq!==row.seq);detail.value=undefined;toast('已忽略这条异常','ok')}
 catch(e){toast(String(e),'danger')}
}
watch(category,()=>void load(false,true));watch(()=>props.issues,v=>{category.value=v?'issues':''})
watch(()=>state.events[0]?.seq,()=>{if(!loading.value)void load()})
watch(currentAccount,()=>{rows.value=[];void load()})
onMounted(()=>{void load();timer=setInterval(()=>void load(),15000)})
onUnmounted(()=>{generation++;if(timer)clearInterval(timer)})
</script>
<template><div class="task-log" :class="{'task-log-compact':compact}">
 <div class="task-log-tools"><div class="log-categories"><button v-for="[key,label] in categories" :key="key" :aria-pressed="category===key" @click="category=key">{{label}}</button></div><div class="log-tools-right"><button v-if="category==='issues'&&entries.length" class="log-bulk" @click="ignoreAll">忽略本页</button><button class="log-refresh" :disabled="loading" title="刷新日志" @click="load()"><Icon name="refresh" :size="16"/></button></div></div>
 <p v-if="error" class="warning">{{error}}</p>
 <div class="task-log-rows"><div v-for="row in entries" :key="row.seq" class="task-log-row" :class="{'has-issue':row.issue}" role="button" tabindex="0" :title="row.issue?'点击查看详情':'查看详情'" @click="detail=row" @keyup.enter="detail=row"><time :title="new Date(row.at).toLocaleString('zh-CN')">{{new Date(row.at).toLocaleTimeString('zh-CN',{hour12:false})}}</time><span class="log-kind" :class="'log-kind-'+row.label">{{row.label}}</span><span class="log-text">{{row.text}}</span><button v-if="row.issue" class="log-dismiss" title="忽略这条异常，不再显示" @click.stop="ignore(row)">忽略</button></div>
 <p v-if="!entries.length" class="empty-state">{{loading?'正在读取…':'这类任务暂无记录'}}</p></div>
 <div v-if="!compact&&entries.length" class="log-more"><button :disabled="loading||!hasMore" @click="load(true)">{{hasMore?'更早记录':'已到最早记录'}}</button></div>
 <Modal v-if="detail" :title="detail.issue?'异常详情':'记录详情'" @close="detail=undefined">
  <div class="log-detail">
   <p class="log-detail-text" :class="{issue:detail.issue}">{{detail.text}}</p>
   <div class="log-detail-meta"><span>#{{detail.seq}}</span><span>{{detail.label}}</span><time>{{new Date(detail.at).toLocaleString('zh-CN')}}</time></div>
   <dl v-if="detailFields.length" class="log-detail-fields"><template v-for="[key,value] in detailFields" :key="key"><dt>{{key}}</dt><dd>{{value}}</dd></template></dl>
   <div class="log-detail-actions"><button @click="detail=undefined">关闭</button><button v-if="detail.issue" class="danger" @click="ignore(detail)">忽略这条</button></div>
  </div>
 </Modal>
</div></template>
<style scoped>
.task-log-tools{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 16px;border-bottom:1px solid var(--line-soft)}.log-categories{display:flex;gap:5px;flex-wrap:wrap}.log-categories button{min-height:28px;padding:4px 9px;font-size:12px;border-color:transparent;background:transparent;color:var(--muted)}.log-categories button[aria-pressed=true]{color:var(--discover);background:var(--discover-soft);border-color:var(--line)}.log-refresh{min-height:28px;padding:4px 7px;background:transparent}.log-tools-right{display:flex;align-items:center;gap:6px;flex:none}.log-bulk{min-height:26px;padding:2px 9px;font-size:var(--text-small);border-radius:999px;border:1px solid var(--line);background:var(--surface);color:var(--muted);cursor:pointer}.log-bulk:hover{color:var(--text);background:var(--surface-2)}.task-log-row{display:grid;grid-template-columns:60px 40px minmax(0,1fr) auto;gap:10px;padding:10px 16px;border-bottom:1px solid var(--line-soft);align-items:baseline;font-size:13px;line-height:1.8;cursor:pointer}.task-log-row:hover{background:var(--surface-2)}.task-log-row:focus-visible{outline:2px solid var(--discover);outline-offset:-2px}.task-log-row time{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}.log-kind{font-size:11px;text-align:center;border-radius:5px;background:var(--surface-2);color:var(--discover)}.log-kind-话题{background:var(--discover-soft)}.log-kind-合集{color:var(--editor);background:var(--editor-soft)}.log-kind-识别{color:var(--accent);background:var(--accent-soft)}.log-kind-核验{color:var(--warn);background:color-mix(in srgb,var(--warn) 13%,var(--surface))}.log-kind-参与{color:var(--accent);background:var(--accent-soft)}.log-text{overflow-wrap:anywhere;white-space:pre-wrap}.has-issue .log-text{color:var(--warn)}.log-dismiss{min-height:24px;padding:2px 9px;font-size:var(--text-small);border-radius:999px;border:1px solid var(--line);background:var(--surface);color:var(--muted);cursor:pointer}.log-dismiss:hover{color:var(--text);background:var(--surface-2)}.log-more{padding:12px;text-align:center}.task-log-compact .task-log-tools{padding:8px 0}.task-log-compact .task-log-row{padding:6px 0}.task-log-compact .task-log-rows{max-height:220px;overflow:auto}
.log-detail{display:grid;gap:12px}.log-detail-text{margin:0;font-size:14px;line-height:1.85;color:var(--text);white-space:pre-wrap;overflow-wrap:anywhere}.log-detail-text.issue{color:var(--warn)}.log-detail-meta{display:flex;gap:12px;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}.log-detail-fields{display:grid;grid-template-columns:auto minmax(0,1fr);gap:6px 14px;margin:0;padding:12px;border-radius:var(--radius-control);background:var(--surface-2);font-size:var(--text-meta);max-height:280px;overflow:auto}.log-detail-fields dt{color:var(--muted)}.log-detail-fields dd{margin:0;overflow-wrap:anywhere;color:var(--text)}.log-detail-actions{display:flex;justify-content:flex-end;gap:10px}
</style>
