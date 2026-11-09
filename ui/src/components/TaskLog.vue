<script setup lang="ts">
import {ref,computed,watch,onMounted,onUnmounted} from 'vue'
import {req} from '../api'
import {state,describeEvent,isIssue,currentAccount} from '../store'
import Icon from './Icon.vue'
const props=defineProps<{compact?:boolean;issues?:boolean}>()
const category=ref(props.issues?'issues':''),loading=ref(false),error=ref(''),hasMore=ref(true)
type Log={seq:number;topic:string;at:string;payload:Record<string,unknown>}
const rows=ref<Log[]>([])
const categories=[['','全部'],['sources','话题 / 合集'],['participation','参与'],['results','开奖 / 清理'],['model','识别'],['issues','异常']]
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
const entries=computed(()=>rows.value.map(r=>({...r,text:describeEvent(r.topic,r.payload),issue:isIssue(r.topic,r.payload),
 label:r.topic==='source.update'?({topic:'话题',collection:'合集'} as Record<string,string>)[String(r.payload.feed_kind)]||'订阅':r.topic==='llm'?'识别':r.topic==='settle'?'核验':r.topic==='write'?'操作':'任务'})))
watch(category,()=>void load(false,true));watch(()=>props.issues,v=>{category.value=v?'issues':''})
watch(()=>state.events[0]?.seq,()=>{if(!loading.value)void load()})
watch(currentAccount,()=>{rows.value=[];void load()})
onMounted(()=>{void load();timer=setInterval(()=>void load(),15000)})
onUnmounted(()=>{generation++;if(timer)clearInterval(timer)})
</script>
<template><div class="task-log" :class="{'task-log-compact':compact}">
 <div class="task-log-tools"><div class="log-categories"><button v-for="[key,label] in categories" :key="key" :aria-pressed="category===key" @click="category=key">{{label}}</button></div><button class="log-refresh" :disabled="loading" title="刷新日志" @click="load()"><Icon name="refresh" :size="16"/></button></div>
 <p v-if="error" class="warning">{{error}}</p>
 <div class="task-log-rows"><div v-for="row in entries" :key="row.seq" class="task-log-row" :class="{'has-issue':row.issue}"><time :title="new Date(row.at).toLocaleString('zh-CN')">{{new Date(row.at).toLocaleTimeString('zh-CN',{hour12:false})}}</time><span class="log-kind" :class="'log-kind-'+row.label">{{row.label}}</span><span class="log-text">{{row.text}}</span></div>
 <p v-if="!entries.length" class="empty-state">{{loading?'正在读取…':'这类任务暂无记录'}}</p></div>
 <div v-if="!compact&&entries.length" class="log-more"><button :disabled="loading||!hasMore" @click="load(true)">{{hasMore?'更早记录':'已到最早记录'}}</button></div>
</div></template>
<style scoped>
.task-log-tools{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 16px;border-bottom:1px solid var(--line-soft)}.log-categories{display:flex;gap:5px;flex-wrap:wrap}.log-categories button{min-height:28px;padding:4px 9px;font-size:12px;border-color:transparent;background:transparent;color:var(--muted)}.log-categories button[aria-pressed=true]{color:var(--discover);background:var(--discover-soft);border-color:var(--line)}.log-refresh{min-height:28px;padding:4px 7px;background:transparent}.task-log-row{display:grid;grid-template-columns:60px 40px minmax(0,1fr);gap:10px;padding:10px 16px;border-bottom:1px solid var(--line-soft);align-items:baseline;font-size:13px;line-height:1.8}.task-log-row time{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}.log-kind{font-size:11px;text-align:center;border-radius:5px;background:var(--surface-2);color:var(--discover)}.log-kind-话题{background:var(--discover-soft)}.log-kind-合集{color:var(--editor);background:var(--editor-soft)}.log-kind-识别{color:var(--accent);background:var(--accent-soft)}.log-text{overflow-wrap:anywhere;white-space:pre-wrap}.has-issue .log-text{color:var(--warn)}.log-more{padding:12px;text-align:center}.task-log-compact .task-log-tools{padding:8px 0}.task-log-compact .task-log-row{padding:6px 0}.task-log-compact .task-log-rows{max-height:220px;overflow:auto}
</style>
