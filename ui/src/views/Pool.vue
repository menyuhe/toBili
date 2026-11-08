<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, type PoolOp, type PoolView, type PoolRunReport } from '../api'
import { toast, currentAccount, updateQueue } from '../store'
import PrizeEditor from '../components/PrizeEditor.vue'
import BlockAuthor from '../components/BlockAuthor.vue'
import Modal from '../components/Modal.vue'
import IconButton from '../components/IconButton.vue'
import ParticipationPace from '../components/ParticipationPace.vue'
const paceOpen=ref(false),loadError=ref('')

const STATE_LABEL: Record<string,string>={pending:'待参与',running:'进行中',success:'已完成',skipped:'已跳过',failed:'失败'}
const owner=currentAccount()
const view=ref<PoolView>({items:[],paused:false,note:'',counts:{},limit:200})
const report=ref<PoolRunReport|null>(null),busy=ref(''),organizing=ref(false)
const resultDialog=ref<HTMLDialogElement|null>(null)
const pausing=ref(false)
async function pauseRun(){pausing.value=true;try{accept(await api.editPool({operation:'pause'}))}catch(error){toast(error instanceof Error?error.message:'暂停失败','danger')}finally{pausing.value=false}}
let generation=0,poll:ReturnType<typeof setInterval>|undefined
const counts=computed(()=>view.value.counts)
const hasFailed=computed(()=>(counts.value.failed||0)>0)
const waiting=computed(()=>(counts.value.pending||0)+(counts.value.running||0))
const finished=computed(()=>(counts.value.success||0)+(counts.value.skipped||0))
function when(iso:string|null){return iso?new Date(iso).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'开奖待定'}
function accept(pool:PoolView){view.value=pool;updateQueue(pool,owner)}
async function load(_quiet=false){const version=++generation;try{const pool=await api.pool();if(version===generation){accept(pool);loadError.value=''}}catch(error){if(version===generation){loadError.value=error instanceof Error?error.message:'读取计划单失败';if(!_quiet)toast(loadError.value,'danger')}}}
async function edit(operation:PoolOp,extra:Partial<{campaign_id:number;direction:number;campaign_ids:number[]}>={}){
 if(busy.value)return
 busy.value=operation;generation++
 try{accept(await api.editPool({operation,...extra}))}catch(error){toast(error instanceof Error?error.message:'操作失败','danger')}finally{busy.value=''}
}
async function run(){
 if(busy.value||!waiting.value||view.value.paused)return
 busy.value='run';generation++
 try{report.value=await api.runPool();const body=report.value;toast(body.note,body.failed?'danger':body.joined?'ok':'info');resultDialog.value?.showModal()}
 catch(error){toast(error instanceof Error?error.message:'执行失败','danger')}
 finally{await load(true);busy.value=''}
}
function clearDone(){if(finished.value)void edit('clear_done')}
onMounted(()=>{void load();poll=setInterval(()=>{if(!busy.value||busy.value==='run')void load(true)},5000)})
onUnmounted(()=>{generation++;if(poll)clearInterval(poll)})
</script>
<template>
<div class="planning-layout">
  <Modal v-if="paceOpen" title="参与节奏" @close="paceOpen=false"><ParticipationPace/></Modal>
  <aside class="panel queue-panel">
    <div class="panel-head"><h2>待参与 <span class="queue-count">{{waiting}}</span></h2><div class="toolbar"><button @click="paceOpen=true">参与节奏</button><button :aria-pressed="organizing" @click="organizing=!organizing">{{organizing?'完成整理':'整理'}}</button></div></div>
    <div class="queue-actions"><button class="primary" :disabled="!!busy||!!view.progress||!waiting||view.paused" @click="run">{{busy==='run'||view.progress?'执行中…':'开始参与'}}</button><button v-if="busy==='run'||view.progress" :disabled="pausing||view.paused" @click="pauseRun">{{view.paused?'正在暂停…':'暂停'}}</button></div>
    <div v-if="view.progress" class="queue-progress" role="status">{{view.progress}}</div>
    <div v-if="view.paused" class="queue-warning"><strong>队列已暂停</strong><p class="text-xs mt-1">{{view.note||'手动暂停'}}</p><div class="toolbar mt-2"><button v-if="hasFailed" :disabled="!!busy" @click="edit('retry')">重试失败</button><button :disabled="!!busy" @click="edit('resume')">继续</button></div></div>
    <p v-if="loadError" class="queue-warning" role="alert">{{loadError}}</p><div class="queue-items"><p v-if="!view.items.length" class="empty-state">从发现奖品加入待参与</p>
      <div v-for="(row,index) in view.items" :key="row.campaign_id" class="queue-item"><span class="queue-index">{{index+1}}</span><div class="min-w-0 flex-1"><a class="queue-title" :href="'https://www.bilibili.com/opus/'+row.dyn_id" target="_blank" rel="noreferrer">{{row.reward||'奖品待核对'}}</a><div class="queue-meta"><span>{{row.up_name}}</span><BlockAuthor :uid="row.up_uid" :name="row.up_name" @saved="load(true)"/><span>· {{when(row.deadline)}}</span><PrizeEditor :id="row.campaign_id" @saved="load(true)"/></div><span v-if="row.state!=='pending'" class="chip" :class="row.state==='failed'?'chip-danger':'chip-ok'">{{STATE_LABEL[row.state]||row.state}}</span><p v-if="row.message" class="text-xs warning mt-1">{{row.message}}</p><div v-if="organizing" class="toolbar mt-2"><button :disabled="!!busy||index===0" @click="edit('move',{campaign_id:row.campaign_id,direction:-1})">上移</button><button :disabled="!!busy||index===view.items.length-1" @click="edit('move',{campaign_id:row.campaign_id,direction:1})">下移</button></div></div><IconButton icon="close" :disabled="!!busy" :label="'移出待参与：'+(row.reward||'奖品待补全')" @click="edit('remove',{campaign_id:row.campaign_id})"/></div>
    </div>
    <details class="queue-more"><summary>队列操作</summary><div class="toolbar mt-3"><button :disabled="!!busy||view.paused" @click="edit('pause')">暂停</button><button :disabled="!!busy||!hasFailed" @click="edit('retry')">重试失败</button><button :disabled="!!busy||!finished" @click="clearDone">移除已完成 / 跳过</button><button v-if="report" @click="resultDialog?.showModal()">上次结果</button></div></details>
  </aside>
  <dialog ref="resultDialog" class="modal"><header><h2>执行结果</h2><button @click="resultDialog?.close()">关闭</button></header><div v-if="report" class="modal-body"><p>{{report.note}}</p><ul class="space-y-3"><li v-for="line in report.lines" :key="line.campaign_id">{{line.title}} · {{STATE_LABEL[line.state]||line.state}}<p v-if="line.message" class="text-xs dim">{{line.message}}</p></li></ul></div></dialog>
</div>
</template>
<style scoped>
.planning-layout{max-width:1280px;margin:auto}.queue-panel{overflow:hidden;border-top:3px solid var(--accent)}
.queue-count{color:var(--accent);margin-left:6px;font-variant-numeric:tabular-nums}.queue-actions{display:flex;gap:10px;padding:18px 22px}
.queue-item{display:flex;gap:12px;align-items:flex-start;padding:20px 22px;border-top:1px solid var(--line-soft)}
.queue-index{font-size:12px;color:var(--muted);padding-top:4px;min-width:20px;flex:none}.queue-title{font-size:var(--text-body);line-height:1.7;color:var(--text);text-decoration:none;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.queue-title:hover{color:var(--accent)}
.queue-item p{margin-top:7px}.queue-item .toolbar{margin-top:8px}.queue-meta{display:flex;align-items:center;flex-wrap:wrap;gap:var(--space-2);margin-top:var(--space-2);font-size:var(--text-meta);color:var(--muted)}
.queue-more{padding:16px 22px;border-top:1px solid var(--line)}.queue-warning{padding:16px 22px;color:var(--warn);background:var(--warn-soft)}
.queue-progress{padding:14px 22px;background:var(--accent-soft);color:var(--accent);border-block:1px solid var(--line);font-variant-numeric:tabular-nums}
</style>
