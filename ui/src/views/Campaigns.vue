<script setup lang="ts">
import DeadlineEditor from '../components/DeadlineEditor.vue'
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {useRoute} from 'vue-router'
import {api,req,type CampaignRow} from '../api'
import {toast,state,currentAccount,updateQueue} from '../store'
import PrizeEditor from '../components/PrizeEditor.vue'
import BlockAuthor from '../components/BlockAuthor.vue'
import ReviewEntry from '../components/ReviewEntry.vue'
import Icon from '../components/Icon.vue'
import IconButton from '../components/IconButton.vue'
import DateField from '../components/DateField.vue'
import Modal from '../components/Modal.vue'
import {deadlineTone,deadlineHint} from '../deadline'
const tableScroller=ref<HTMLElement>(),clockNow=ref(Date.now())
const route=useRoute(),rows=ref<CampaignRow[]>([]),total=ref(0),page=ref(0),loading=ref(false),busy=ref(false)
const counts=ref<Record<string,number>>({})
const keyword=ref(''),kinds=ref<string[]>([]),statuses=ref<string[]>([]),timeField=ref('published'),after=ref(''),before=ref(''),order=ref('newest'),selected=ref<number[]>([]),filtersOpen=ref(false)
/* 搜索是个图标按钮，点了才展开输入框。它平时不该一直占着 230px 的横向空间，
   但也不能藏进筛选弹窗：搜一个关键词是最常用的动作，多一次点击就够烦了。 */
const searchOpen=ref(false),searchInput=ref<HTMLInputElement|null>(null)
function toggleSearch(){searchOpen.value=!searchOpen.value;if(searchOpen.value)requestAnimationFrame(()=>searchInput.value?.focus())}
/** 收起搜索框时把关键词一并清掉：框都看不见了却还在过滤，是最难自查的状态。 */
function closeSearch(){searchOpen.value=false;if(keyword.value){keyword.value='';search()}}
const manualReview=computed(()=>route.query.prizes==='unknown')
const reviewBusy=ref(false),prizeToolsOpen=ref(false)
const filterCount=computed(()=>kinds.value.length+statuses.value.length+Number(missingOnly.value)+Number(onlyOpen.value)+Number(officialOnly.value)+Number(!!after.value)+Number(!!before.value))
const section=computed(()=>manualReview.value?'prizes':route.path==='/shelf'?'shelved':route.path==='/cleaned'?'cleaned':'')
/** 三个页签共用这一个列表：发现奖品 / 往期架 / 已清理。标题和说明跟着 section 走。 */
const heading=computed(()=>{
  if(manualReview.value)return {title:'待补全活动奖品',hint:'奖品名识别不出来，需要人补一次'}
  if(section.value==='shelved')return {title:'往期架',hint:'已截止或不打算参加的活动收在这里，随时可以取回'}
  if(section.value==='cleaned')return {title:'已清理',hint:'转发已删除的活动，记录留着备查'}
  return {title:'发现奖品',hint:'未参加的排在前面，按截止时间可以从紧到松'}
})
const KIND_LABEL:Record<string,string>={interactive:'互动抽奖',share:'转发抽奖',comment:'转发抽奖',reserve:'预约',check_in:'打卡签到'}
/** 转发抽奖与评论抽奖是一类：参与动作都是关注 + 转发 + 评论 + 收藏，只是正文措辞
 *  一个写"转发"、一个写"评论"。库里仍保留两个原始值（那是正文推断的结论，不该抹掉），
 *  但列表上只该出现一个类型、一个筛选框，所以筛选按组合值下发（catalog 支持逗号多选）。 */
const FILTER_KINDS=[{key:'share,comment',tone:'share',label:'转发抽奖'},{key:'interactive',tone:'interactive',label:'互动抽奖'},{key:'reserve',tone:'reserve',label:'预约抽奖'}]
const STATES:Record<string,string>={unjoined:'未参加',enrolled:'已参加',pending:'待开奖',won:'中奖',lost:'未中奖'}
const missingOnly=ref(false),onlyOpen=ref(false),officialOnly=ref(false)
const prize=ref<{enabled:boolean;total:number;counts:Record<string,number>;attention:number;note:string}>()
const pages=computed(()=>Math.max(1,Math.ceil(total.value/40)))
const expired=(r:CampaignRow)=>r.draw_state==='ended'||!!(r.expires_at||r.deadline_at)&&Date.parse((r.expires_at||r.deadline_at)!)<=Date.now()
const eligible=(r:CampaignRow)=>!section.value&&!r.enrollment_id&&!r.queued&&!expired(r)&&!['unverified','unsupported'].includes(r.draw_state||'')
const all=computed({get:()=>rows.value.some(eligible)&&rows.value.filter(eligible).every(r=>selected.value.includes(r.id)),set:(v:boolean)=>selected.value=v?rows.value.filter(eligible).map(r=>r.id):[]})
/** 顶上的五张卡：既是数字，也是筛选器。
 *  数量由后端在"不按参与结果过滤"的集合上算（见 catalog 的 status_at），
 *  否则点一下「中奖」，另外四张全变 0，就再也跳不回去了。 */
const METRICS=[
 {key:'unjoined',count:'unjoined',label:'未参加',hint:'还没加入待参与',tone:'metric-tone-accent'},
 {key:'enrolled',count:'joined',label:'已参加',hint:'报过名，等开奖',tone:'metric-tone-discover'},
 {key:'pending',count:'pending',label:'待开奖',hint:'还没到开奖时间',tone:'metric-tone-warn'},
 {key:'won',count:'won',label:'中奖',hint:'已确认中奖',tone:'metric-tone-ok'},
 {key:'lost',count:'lost',label:'未中奖',hint:'已确认没中',tone:'metric-tone-danger'},
]
function pickStatus(key:string){
  statuses.value=statuses.value.includes(key)?[]:[key]
  search()
}
let version=0,poll:ReturnType<typeof setInterval>|undefined
function iso(s:string){return s?new Date(s).toISOString():''}
async function load(){const v=++version;loading.value=true;try{const d=await api.campaigns({keyword:keyword.value,kind:kinds.value.join(','),statuses:statuses.value.join(','),state:section.value,time_field:timeField.value,after:iso(after.value),before:iso(before.value),order:order.value,prize_state:(manualReview.value||missingOnly.value)?'unknown':'',availability:onlyOpen.value?'open':'',official:officialOnly.value?'yes':'',limit:40,offset:page.value*40});if(v===version){rows.value=d.items;total.value=d.total;counts.value=d.counts||{};if(page.value>=pages.value){page.value=pages.value-1;void load();return;}selected.value=selected.value.filter(id=>d.items.some(r=>r.id===id&&eligible(r)))}}catch(e){toast(String(e),'danger')}finally{if(v===version)loading.value=false}}
function search(){page.value=0;void load();if(tableScroller.value)tableScroller.value.scrollTop=0}
function toggleKind(key:string){kinds.value=kinds.value.includes(key)?kinds.value.filter(k=>k!==key):[...kinds.value,key];search()}
async function turnPage(delta:number){page.value+=delta;await load();if(tableScroller.value)tableScroller.value.scrollTop=0}
function resetFilters(){missingOnly.value=manualReview.value;onlyOpen.value=false;officialOnly.value=false;keyword.value='';kinds.value=[];statuses.value=[];after.value='';before.value='';timeField.value='published';order.value='newest'}
function reset(){resetFilters();search()}
function when(s:string|null){if(!s)return '未提供';return new Date(s).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})}
function conclusion(r:CampaignRow){if(r.draw_state==='ended')return '已开奖 / 截止';if(['unverified','unsupported'].includes(r.draw_state||''))return '待核对';return r.cleaned?'已清理':r.shelf_reason==='manual'&&!r.enrollment_id?'手动清理':!r.enrollment_id?(r.shelf_reason==='expired'||expired(r)?'日期截止':'未参加'):r.verdict==='won'?'中奖':r.verdict==='lost'?'未中奖':['blocked','failed','planned'].includes(r.enrollment_stage||'')?'参与未完成':'待开奖'}
async function dismiss(r:CampaignRow){const owner=currentAccount();busy.value=true;try{await api.dismiss(r.id,manualReview.value);updateQueue(await api.pool(),owner);await load();await progress();toast('已移到往期架','ok')}catch(e){toast(String(e),'danger')}finally{busy.value=false}}
async function add(ids:number[]){const owner=currentAccount();busy.value=true;try{const pool=await api.editPool({operation:'add',campaign_ids:ids});updateQueue(pool,owner);selected.value=[];await load();toast('已加入待参与','ok')}catch(e){toast(String(e),'danger')}finally{busy.value=false}}
async function shelf(r?:CampaignRow){busy.value=true;try{if(r)await api.restore(r.id);else{if(!confirm('将全部已截止活动收入往期架？'))return;await api.shelve()}await load()}catch(e){toast(String(e),'danger')}finally{busy.value=false}}
async function progress(){try{prize.value=await req('/api/campaigns/prizes')}catch{}}
async function review(){reviewBusy.value=true;try{prize.value=await req('/api/campaigns/prizes?enabled='+!prize.value?.enabled,{method:'POST'});toast(prize.value?.enabled?'自动核对已开启':'自动核对已暂停','ok')}catch(e){toast(String(e),'danger')}finally{reviewBusy.value=false}}
const account=state.overview?.accounts.find(r=>r.current)?.id??'anon'
let storageKey=`discovery:${account}:${route.fullPath}`
function remember(){sessionStorage.setItem(storageKey,JSON.stringify({keyword:keyword.value,kinds:kinds.value,statuses:statuses.value,timeField:timeField.value,after:after.value,before:before.value,order:order.value,page:page.value,missingOnly:missingOnly.value,onlyOpen:onlyOpen.value,officialOnly:officialOnly.value}))}
function restore(){try{const saved=JSON.parse(sessionStorage.getItem(storageKey)||'{}');for(const [key,target] of Object.entries({keyword,kinds,statuses,timeField,after,before,order,page,missingOnly,onlyOpen,officialOnly})){if(saved[key]!==undefined)target.value=saved[key]}/** 旧会话里存的是 comment 这个单值，合并后它已不是筛选项，折进来免得出现"勾不上却仍在过滤" */kinds.value=(kinds.value||[]).map(k=>k==='comment'?'share,comment':k).filter((k,i,a)=>a.indexOf(k)===i)}catch{}}
watch(()=>route.fullPath,()=>{remember();storageKey=`discovery:${account}:${route.fullPath}`;page.value=0;selected.value=[];filtersOpen.value=false;searchOpen.value=false;resetFilters();restore();void load()})
onMounted(()=>{resetFilters();restore();void load();void progress();poll=setInterval(()=>{clockNow.value=Date.now();void progress();if(!busy.value)void load()},30000)})
onUnmounted(()=>{remember();if(poll)clearInterval(poll)})
</script>
<template><div class="campaign-workspace">
<section class="panel page-heading">
  <div><h2>{{heading.title}}</h2><span class="heading-sub">{{heading.hint}} · 共 {{total}} 条<template v-if="filterCount"> · 已筛 {{filterCount}} 项</template></span></div>
  <div class="toolbar">
    <button class="tool-icon" :class="{on:searchOpen||!!keyword}" title="搜索活动奖品、UP 或动态号" aria-label="搜索" @click="toggleSearch"><Icon name="search" :size="16"/></button>
    <button :class="{primary:filterCount>0}" @click="filtersOpen=true"><Icon name="filter" :size="16"/>筛选<span v-if="filterCount"> · {{filterCount}}</span></button>
    <button v-if="!section" class="primary" :disabled="busy||!selected.length" @click="add(selected)">加入待参与{{selected.length?' · '+selected.length:''}}</button>
    <button v-if="!section" :disabled="busy" @click="shelf()"><Icon name="archive" :size="16"/>收存已截止</button>
    <button v-if="!section||manualReview" @click="prizeToolsOpen=true"><Icon name="gift" :size="16"/>奖品识别<span v-if="prize?.attention"> · {{prize.attention}}</span></button>
  </div>
  <div v-if="searchOpen" class="search-strip">
    <input ref="searchInput" v-model="keyword" class="ipt" placeholder="活动奖品、UP 或动态号" aria-label="搜索活动奖品" @keyup.enter="search" @keyup.esc="closeSearch"/>
    <button @click="search"><Icon name="search" :size="16"/>搜索</button>
    <button class="mini" @click="closeSearch">取消</button>
  </div>
</section>

<div class="metric-row">
  <button v-for="card in METRICS" :key="card.key" class="panel metric-card" :class="[card.tone,{selected:statuses.includes(card.key)}]" @click="pickStatus(card.key)">
    <span>{{card.label}}</span><strong>{{counts[card.count]??0}}</strong><small>{{card.hint}}</small>
  </button>
</div>

<section class="panel list-panel campaign-panel">
<div ref="tableScroller" class="list-scroll"><table class="dense campaign-table"><thead><tr><th v-if="!section" class="check-col"><input type="checkbox" v-model="all" :disabled="busy" aria-label="选择本页可参与活动奖品"/></th><th class="cell-align-start">活动奖品</th><th class="up-col">UP</th><th class="type-col">类型</th><th class="date-col">截止时间</th><th class="state-col">结论</th><th class="action-col">本地安排</th></tr></thead><tbody><tr v-for="r in rows" :key="r.id"><td v-if="!section"><input type="checkbox" v-model="selected" :value="r.id" :disabled="busy||!eligible(r)" :aria-label="'选择 '+(r.reward||'奖品待补全')"/></td><td class="prize-cell cell-align-start"><a :href="'https://t.bilibili.com/'+r.dyn_id" target="_blank" rel="noreferrer" :title="r.reward||'奖品待补全'">{{r.reward||'奖品待补全'}}</a><small>发布：{{when(r.published_at)}}<PrizeEditor :id="r.id" @saved="load();progress()"/></small></td><td class="author-cell"><div class="author-inline"><a class="author-name" :href="r.up_uid?'https://space.bilibili.com/'+r.up_uid:undefined" :target="r.up_uid?'_blank':undefined" rel="noreferrer" :title="r.up_uid?'在 B 站打开 '+(r.up_name||r.up_uid)+' 的主页':'没有 UP 信息'">{{r.up_name||'—'}}</a><BlockAuthor :uid="r.up_uid" :name="r.up_name" @saved="load();progress()"/></div></td><td><span class="kind-badge" :class="'kind-'+r.kind">{{KIND_LABEL[r.kind]||r.kind}}</span></td><td class="date-cell"><div class="deadline-inline"><span class="deadline-mark" :class="deadlineTone(r.deadline_at,clockNow)" :title="deadlineHint(deadlineTone(r.deadline_at,clockNow))">{{r.deadline_at?when(r.deadline_at):'未提供'}}</span><DeadlineEditor :id="r.id" @saved="load"/></div></td><td :title="r.draw_reason"><span class="chip" :class="r.cleaned?'chip-accent':r.verdict==='won'?'chip-ok':r.verdict==='lost'?'chip-danger':''">{{conclusion(r)}}</span></td><td><div class="row-controls"><button v-if="section==='shelved'" :disabled="busy" @click="shelf(r)">取回</button><RouterLink v-else-if="r.queued" to="/pool" class="chip chip-ok">查看计划</RouterLink><button v-else-if="eligible(r)" :disabled="busy" @click="add([r.id])">加入</button><IconButton v-if="manualReview||!section&&!r.enrollment_id" icon="archive" :label="manualReview?'移出补全列表，收入往期架':'不参加，移到往期架'" :disabled="busy" @click="dismiss(r)"/><ReviewEntry v-if="r.enrollment_id" :dynamic="r.dyn_id" @saved="load"/><span v-if="r.kept" class="chip">保留</span></div></td></tr><tr v-if="!rows.length"><td :colspan="section?6:7" class="empty-state">{{loading?'读取中…':'没有匹配的活动奖品'}}</td></tr></tbody></table></div>
<footer class="list-footer"><span>{{total}} 条</span><button :disabled="!page||loading" @click="turnPage(-1)">上一页</button><span>{{page+1}} / {{pages}}</span><button :disabled="page+1>=pages||loading" @click="turnPage(1)">下一页</button></footer></section>

<Modal v-if="filtersOpen" title="筛选活动奖品" @close="filtersOpen=false"><div class="filter-grid">
<div class="filter-group"><h3>范围</h3><div class="compact-options"><label><input type="checkbox" v-model="missingOnly" :disabled="manualReview" @change="search"/>奖品待补全</label><label><input type="checkbox" v-model="onlyOpen" @change="search"/>仅未截止</label><label><input type="checkbox" v-model="officialOnly" @change="search"/>仅官方已确认</label></div></div>
<div class="filter-group"><h3>参与类型</h3><div class="compact-options"><label v-for="item in FILTER_KINDS" :key="item.key"><input type="checkbox" :checked="kinds.includes(item.key)" @change="toggleKind(item.key)"/>{{item.label}}</label></div></div>
<div class="filter-group"><h3>参与结果</h3><div class="compact-options"><label v-for="(label,key) in STATES" :key="key"><input type="checkbox" v-model="statuses" :value="key" @change="search"/>{{label}}</label></div></div>
<div class="filter-group"><h3>排序</h3><div class="compact-options"><select class="ipt" v-model="order" aria-label="排序方式" style="width:220px" @change="search"><option value="newest">未参加优先 · 最新发布</option><option value="deadline">未参加优先 · 最早截止</option></select></div></div>
<div class="filter-group"><h3>时间</h3><div class="time-filters"><select class="ipt" v-model="timeField" @change="search"><option value="published">发布时间</option><option value="deadline">截止时间</option></select><DateField v-model="after" time label="起始时间" @change="search"/><span>至</span><DateField v-model="before" time label="结束时间" @change="search"/></div></div>
</div><div class="modal-actions"><button @click="reset">重置</button><button class="primary" @click="filtersOpen=false">显示 {{total}} 条</button></div></Modal>
<Modal v-if="prizeToolsOpen" title="奖品识别" @close="prizeToolsOpen=false"><div class="prize-progress" role="status"><Icon name="gift" :size="18"/>

<span>{{prize?.enabled?(prize?.counts.pending?'自动识别中':'本轮识别结束'):'自动识别已暂停'}}</span><span class="muted">已识别 {{(prize?.counts.verified||0)+(prize?.counts.manual||0)}} · 待识别 {{prize?.counts.pending||0}}</span><RouterLink class="review-link" to="/campaigns?prizes=unknown" @click="prizeToolsOpen=false">手动补全 {{prize?.attention||0}} 条 ↗</RouterLink><button class="mini" :disabled="reviewBusy" @click="review">{{reviewBusy?'更新中…':prize?.enabled?'暂停自动识别':'开启自动识别'}}</button>
<span v-if="prize?.note" class="warning">{{prize.note.replace(/^RemoteError:\s*(\[[^\]]+\]\s*)?/, '')}}</span></div></Modal>
</div></template>
<style scoped>
.campaign-workspace{height:100%;min-height:520px;max-width:1440px;margin:auto;display:flex;flex-direction:column;gap:18px}
.review-link{color:var(--discover);font-weight:600;text-decoration:none}.review-link:hover{text-decoration:underline}.prize-progress .mini{margin-left:auto}
.time-filters{display:grid;grid-template-columns:minmax(0,1fr) 20px minmax(0,1fr);gap:10px;align-items:center}.time-filters select{grid-column:1/-1;width:130px}.time-filters .date-field{min-width:0;width:100%}
.prize-progress{flex:none;padding:12px 18px;border:1px solid var(--line);border-radius:var(--radius-panel);display:flex;align-items:center;flex-wrap:wrap;gap:14px;background:var(--surface);font-size:var(--text-meta)}
.campaign-panel{flex:1;min-height:0}
.campaign-table{width:100%;min-width:1000px;table-layout:fixed}
.campaign-table td,.campaign-table th{text-align:center;vertical-align:middle;white-space:normal}
.campaign-table .prize-cell{text-align:left;min-width:260px}
.prize-cell>a{text-decoration:none;font-weight:600;font-size:var(--text-body);color:var(--text);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;line-height:1.7}
.prize-cell small{display:block;margin-top:8px;color:var(--muted);font-size:var(--text-small)}.prize-cell small a{color:var(--discover);margin-left:10px}
.check-col{width:42px}.campaign-table th.check-col,.campaign-table td:has(>input[type=checkbox]){padding:10px 8px;text-overflow:clip;overflow:visible}
.up-col{width:210px}.campaign-table .author-cell{min-width:210px;max-width:250px;padding-inline:12px}
.author-name{display:block;min-width:0;white-space:normal;overflow-wrap:break-word;line-height:1.5;color:var(--text);text-decoration:none}
.author-name:hover{color:var(--discover);text-decoration:underline}
.author-inline{display:flex;align-items:center;justify-content:center;gap:6px}
.type-col{width:100px}.campaign-table td:has(>.kind-badge){min-width:100px;padding-inline:10px}
.date-col{width:195px}.state-col{width:120px}.action-col{width:125px}
.deadline-inline{display:flex;align-items:center;justify-content:center;gap:2px}
.campaign-table .chip{max-width:none;white-space:nowrap}.campaign-table td{overflow:visible}
.row-controls{flex-wrap:wrap}
.date-cell{font-variant-numeric:tabular-nums;white-space:nowrap!important;font-size:var(--text-meta)}
/* 统一底色：行不再按类型染色。类型由「类型」列那枚徽章和上面的类型按钮表达，
   一张表里五种底色会把"扫一眼看结尾"变成"先分辨颜色"，反而更慢。 */
.campaign-table tbody td{border-bottom:1px solid var(--line-soft)}
.campaign-table tbody tr:last-child td{border-bottom:0}
.campaign-table tbody tr:hover{background:var(--surface-2)}
</style>
