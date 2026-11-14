<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, type PoolOp, type PoolView, type PoolRunReport } from '../api'
import { toast, currentAccount, updateQueue } from '../store'
import PrizeEditor from '../components/PrizeEditor.vue'
import BlockAuthor from '../components/BlockAuthor.vue'
import Modal from '../components/Modal.vue'
import IconButton from '../components/IconButton.vue'
import Icon from '../components/Icon.vue'
import ParticipationPace from '../components/ParticipationPace.vue'
const paceOpen=ref(false),loadError=ref('')

const STATE_LABEL: Record<string,string>={pending:'待参与',running:'进行中',success:'已完成',skipped:'已跳过',failed:'失败',review:'待处理'}
const listMode=ref<'pending'|'review'>('pending')
const owner=currentAccount()
const view=ref<PoolView>({items:[],paused:false,note:'',counts:{},limit:200})
const report=ref<PoolRunReport|null>(null),busy=ref(''),organizing=ref(false)
const resultDialog=ref<HTMLDialogElement|null>(null)
const pausing=ref(false)
async function pauseRun(){pausing.value=true;try{accept(await api.editPool({operation:'pause'}))}catch(error){toast(error instanceof Error?error.message:'暂停失败','danger')}finally{pausing.value=false}}
let generation=0,poll:ReturnType<typeof setInterval>|undefined
const counts=computed(()=>view.value.counts)
const reviewCount=computed(()=>(counts.value.review||0)+(counts.value.failed||0))
const visibleItems=computed(()=>view.value.items.filter(row=>listMode.value==='review'?['review','failed'].includes(row.state):!['review','failed'].includes(row.state)))
const hasFailed=computed(()=>(counts.value.failed||0)>0)
const waiting=computed(()=>(counts.value.pending||0)+(counts.value.running||0))
const finished=computed(()=>(counts.value.success||0)+(counts.value.skipped||0))
/** 顶上四张卡：既是数字，也是切换器。已完成 / 已跳过只报数，不能点 ——
 *  队列里那些行已经不在表里了，点进去只会得到一张空表。 */
type MetricCard={key:string;label:string;count:()=>number;hint:string;tone:string;mode:'pending'|'review'|''}
const METRICS:MetricCard[]=[
 {key:'pending',label:'待参与',count:()=>waiting.value,hint:'排在最前面先执行',tone:'metric-tone-accent',mode:'pending' as const},
 {key:'review',label:'待处理',count:()=>reviewCount.value,hint:'失败或需要人看一眼',tone:'metric-tone-warn',mode:'review' as const},
 {key:'done',label:'已完成',count:()=>counts.value.success||0,hint:'已报名成功',tone:'metric-tone-ok',mode:''},
 {key:'skipped',label:'已跳过',count:()=>counts.value.skipped||0,hint:'条件不满足，主动跳过',tone:'metric-tone-discover',mode:''},
]
function pick(mode:'pending'|'review'|''){if(mode)listMode.value=mode}
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
<div class="pool-workspace">
  <Modal v-if="paceOpen" title="参与节奏" @close="paceOpen=false"><ParticipationPace/></Modal>

  <section class="panel page-heading">
    <div><h2>待参与</h2><span class="heading-sub">按队列顺序执行，一条失败不影响后面的 {{waiting}} 条</span></div>
    <div class="toolbar">
      <button @click="paceOpen=true"><Icon name="clock" :size="16"/>参与节奏</button>
      <button :aria-pressed="organizing" :class="{primary:organizing}" @click="organizing=!organizing">{{organizing?'完成整理':'调整顺序'}}</button>
      <button class="primary" :disabled="!!busy||!!view.progress||!waiting||view.paused" @click="run">{{busy==='run'||view.progress?'执行中…':'开始参与'}}</button>
      <button v-if="busy==='run'||view.progress" class="danger" :disabled="pausing||view.paused" @click="pauseRun">{{view.paused?'正在暂停…':'暂停'}}</button>
    </div>
  </section>

  <div class="metric-row">
    <button v-for="card in METRICS" :key="card.key" class="panel metric-card" :class="[card.tone,{selected:!!card.mode&&listMode===card.mode,static:!card.mode}]" @click="pick(card.mode)">
      <span>{{card.label}}</span><strong>{{card.count()}}</strong><small>{{card.hint}}</small>
    </button>
  </div>

  <!-- 这里原本还有一行「待参与 / 待处理」切换。上面那四张卡点一下就做同一件事，
       两套开关指同一份状态，纯属重复，删掉把高度还给表格。 -->
  <section class="panel list-panel">
    <div v-if="view.progress" class="queue-banner queue-progress" role="status">{{view.progress}}</div>
    <div v-if="view.paused" class="queue-banner queue-warning"><strong>队列已暂停</strong><span>{{view.note||'手动暂停'}}</span><div class="toolbar"><button class="mini" v-if="hasFailed" :disabled="!!busy" @click="edit('retry')">重试失败</button><button class="mini" :disabled="!!busy" @click="edit('resume')">继续</button></div></div>
    <p v-if="loadError" class="queue-banner queue-warning" role="alert">{{loadError}}</p>
    <div class="list-scroll">
      <table class="dense pool-table">
        <thead><tr><th class="pos-col">#</th><th class="cell-align-start">活动奖品</th><th class="up-col">UP</th><th class="date-col">截止时间</th><th class="state-col">状态</th><th class="action-col">操作</th></tr></thead>
        <tbody>
          <tr v-for="(row,index) in visibleItems" :key="row.campaign_id">
            <td class="pos-col tabular-nums">{{index+1}}</td>
            <td class="cell-align-start prize-cell"><a :href="'https://www.bilibili.com/opus/'+row.dyn_id" target="_blank" rel="noreferrer">{{row.reward||'奖品待核对'}}</a><small v-if="row.message" class="warning">{{row.message}}</small><PrizeEditor :id="row.campaign_id" @saved="load(true)"/></td>
            <td><div class="author-inline"><span class="author-name">{{row.up_name||'—'}}</span><BlockAuthor :uid="row.up_uid" :name="row.up_name" @saved="load(true)"/></div></td>
            <td class="date-cell">{{when(row.deadline)}}</td>
            <td><span class="chip" :class="['failed','review'].includes(row.state)?'chip-warn':row.state==='success'?'chip-ok':''">{{STATE_LABEL[row.state]||row.state}}</span></td>
            <td><div class="row-controls">
              <template v-if="listMode==='review'"><button class="mini" :disabled="!!busy" @click="edit('retry',{campaign_id:row.campaign_id})">重新排队</button></template>
              <template v-else-if="organizing"><button class="mini" :disabled="!!busy||row.position===0" @click="edit('move',{campaign_id:row.campaign_id,direction:-1})">上移</button><button class="mini" :disabled="!!busy||row.position===view.items.length-1" @click="edit('move',{campaign_id:row.campaign_id,direction:1})">下移</button></template>
              <IconButton icon="close" :disabled="!!busy" :label="'移出待参与：'+(row.reward||'奖品待补全')" @click="edit('remove',{campaign_id:row.campaign_id})"/>
            </div></td>
          </tr>
          <tr v-if="!visibleItems.length"><td colspan="6" class="empty-state">{{listMode==='review'?'没有待处理项':'从发现奖品加入待参与'}}</td></tr>
        </tbody>
      </table>
    </div>
    <footer class="list-footer pool-footer">
      <details class="queue-more"><summary>队列操作</summary><div class="toolbar"><button class="mini" :disabled="!!busy||view.paused" @click="edit('pause')">暂停</button><button class="mini" :disabled="!!busy||!hasFailed" @click="edit('retry')">重试失败</button><button class="mini" :disabled="!!busy||!finished" @click="clearDone">移除已完成 / 跳过</button><button v-if="report" class="mini" @click="resultDialog?.showModal()">上次结果</button></div></details>
      <span class="queue-limit">已列出 {{visibleItems.length}} 条 · 队列上限 {{view.limit}} 条</span>
    </footer>
  </section>

  <dialog ref="resultDialog" class="modal"><header><h2>执行结果</h2><button @click="resultDialog?.close()">关闭</button></header><div v-if="report" class="modal-body"><p>{{report.note}}</p><ul class="space-y-3"><li v-for="line in report.lines" :key="line.campaign_id">{{line.title}} · {{STATE_LABEL[line.state]||line.state}}<p v-if="line.message" class="text-xs dim">{{line.message}}</p></li></ul></div></dialog>
</div>
</template>
<style scoped>
.pool-workspace{max-width:1440px;margin:auto;display:flex;flex-direction:column;gap:18px;height:100%;min-height:520px}
.pool-workspace>.list-panel{flex:1;min-height:0}
.pool-table{width:100%;min-width:900px;table-layout:fixed}
.pool-table td,.pool-table th{text-align:center;vertical-align:middle;white-space:normal}
.pool-table tbody td{border-bottom:1px solid var(--line-soft)}
.pool-table tbody tr:last-child td{border-bottom:0}
.pool-table tbody tr:hover{background:var(--surface-2)}
.pos-col{width:56px;color:var(--muted);font-size:var(--text-meta)}
.prize-cell{min-width:320px;padding-inline:16px!important}
.prize-cell>a{font-weight:600;font-size:var(--text-body);color:var(--text);text-decoration:none;line-height:1.6}
.prize-cell>a:hover{color:var(--accent)}
.prize-cell small{display:block;margin-top:6px;font-size:var(--text-small)}
.up-col{width:200px}.date-col{width:190px}.state-col{width:104px}.action-col{width:190px}
.author-inline{display:flex;align-items:center;justify-content:center;gap:6px}
.author-name{min-width:0;overflow-wrap:break-word;line-height:1.5}
.date-cell{font-variant-numeric:tabular-nums;font-size:var(--text-meta)}
.queue-banner{display:flex;align-items:center;flex-wrap:wrap;gap:12px;flex:none;padding:12px 18px;font-size:var(--text-meta);border-bottom:1px solid var(--line)}
.queue-progress{background:var(--accent-soft);color:var(--accent)}
.queue-warning{background:var(--warn-soft);color:var(--warn)}
.queue-footer{justify-content:space-between;flex-wrap:wrap}
.queue-more summary{font-size:var(--text-meta);color:var(--muted)}
.queue-more>div{margin-top:10px}
.queue-limit{font-size:var(--text-meta);color:var(--muted)}
.metric-card.static{cursor:default}
.metric-card.static:hover{background:var(--surface)}
</style>
