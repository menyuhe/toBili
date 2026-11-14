<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
import {api,req} from '../api'
import {state,toast} from '../store'
import ReviewEntry from '../components/ReviewEntry.vue'
import Modal from '../components/Modal.vue'
import Icon from '../components/Icon.vue'
type Row={check_error:string;campaign_id:number;dyn_id:string;title:string;reward:string;up_name:string;up_uid:string;outcome:string;share_id:string;link_state:string;choices:Record<string,string>;evidence:string;checked_at:string;protected:boolean;cleaned:boolean}
type Ledger={items:Row[];total:number;all_total:number;outcomes:Record<string,number>;links:Record<string,number>;cleanup:Record<string,number>;scan:{pages?:number;scanned?:number;cursor?:string;finished?:boolean}}
const data=ref<Ledger>(),busy=ref(''),query=ref(''),outcome=ref(''),link=ref(''),page=ref(0),error=ref('')
const discarded=ref<Record<string,any>[]>([]),discardOpen=ref(false)
async function showDiscarded(){try{discarded.value=(await req<{items:Record<string,any>[]}>('/api/results/discarded')).items;discardOpen.value=true}catch(e){toast(String(e),'danger')}}
async function discardRow(cid:number){busy.value='discard';try{const r=await req<{note:string;state:string}>(`/api/results/${cid}/discard`,{method:'POST',body:JSON.stringify({account_id:owner.value})});toast(r.note,r.state==='done'?'ok':'warn');await load();if(discardOpen.value)await showDiscarded()}catch(e){toast(String(e),'danger')}finally{busy.value=''}}
const history=ref<Record<string,string>[]>([]),historyOpen=ref(false),historyPage=ref(0),chosen=ref<Row>(),selected=ref('')
const owner=computed(()=>state.overview?.accounts.find(a=>a.current)?.id||0)
const outcomes:Record<string,string>={pending:'待开奖',unknown:'待确认',won:'中奖',lost:'未中奖',void:'作废'}
/** 「缺 UP 信息」单独一档：这类永远扫不到转发，混在「未扫描」里那个数就一直不动，
 *  而侧栏红点数正是按它们算的 —— 两边对不上，人就会觉得红点是乱报的。 */
const links:Record<string,string>={verified:'已核对',candidate:'待核对',conflict:'多条候选',blocked:'缺 UP 信息',missing:'本次未找到',unscanned:'未扫描'}
const bucket=(row:Row)=>row.check_error==='缺少 UP 信息'?'blocked':row.link_state
/** 五档结果各一色，和列表里的标签同色系。卡片本身是筛选器，颜色即含义。 */
const TONE:Record<string,string>={pending:'metric-tone-discover',unknown:'metric-tone-warn',won:'metric-tone-accent',lost:'metric-tone-danger',void:'metric-tone-editor'}
/* 筛选条原本占着表格上方两行，而这一页真正每天要点的只有「核验开奖」。
   把它折进一个按钮：收起时只剩标题行，展开时才占位置。 */
const searchOpen=ref(false),filtersOpen=ref(false),searchInput=ref<HTMLInputElement|null>(null)
const filterCount=computed(()=>Number(!!query.value)+Number(!!outcome.value)+Number(!!link.value))
function toggleSearch(){searchOpen.value=!searchOpen.value;if(searchOpen.value)requestAnimationFrame(()=>searchInput.value?.focus())}
/** 收起搜索框要一并清关键词：框看不见却还在过滤，是最难自查的状态。 */
function closeSearch(){searchOpen.value=false;if(query.value){query.value='';filter()}}
function clearFilters(){query.value='';outcome.value='';link.value='';filter()}
let generation=0,alive=true,poll:ReturnType<typeof setInterval>|undefined
async function load(){const request=++generation;try{const r=await req<Ledger>(`/api/results?query=${encodeURIComponent(query.value)}&outcome=${outcome.value}&link=${link.value}&offset=${page.value*50}`);if(alive&&request===generation){data.value=r;error.value=''}}catch(e){if(alive&&request===generation)error.value=String(e)}}
function filter(){page.value=0;void load()}
async function settle(){busy.value='settle';try{const r=await api.settle(true,30);toast(`核验 ${r.checked} 条，更新 ${r.decided} 条`,r.problems.length?'warn':'ok');await load();if(r.problems.length)error.value=r.problems.join('；')}catch(e){toast(String(e),'danger')}finally{busy.value=''}}
async function match(resume=false){busy.value='match';try{await req('/api/results/match',{method:'POST',body:JSON.stringify({account_id:owner.value,resume})});await load();toast('转发匹配已更新','ok')}catch(e){toast(String(e),'danger')}finally{busy.value=''}}
async function showHistory(delta=0){historyPage.value=Math.max(0,historyPage.value+delta);try{history.value=(await req<{items:Record<string,string>[]}>(`/api/results/history?offset=${historyPage.value*50}`)).items;historyOpen.value=true}catch(e){toast(String(e),'danger')}}
async function saveLink(){if(!chosen.value||!selected.value)return;busy.value='link';try{await req(`/api/results/${chosen.value.campaign_id}/link`,{method:'PUT',body:JSON.stringify({account_id:owner.value,share_id:selected.value})});chosen.value=undefined;await load()}catch(e){toast(String(e),'danger')}finally{busy.value=''}}
function choose(row:Row){chosen.value=row;selected.value=Object.keys(row.choices)[0]||''}
onMounted(()=>{void load();poll=setInterval(()=>{if(!busy.value)void load()},30000)})
onUnmounted(()=>{alive=false;if(poll)clearInterval(poll)})
</script>
<template><div class="results-workspace">
 <section class="panel page-heading"><div><h2>开奖核验</h2><span class="heading-sub">{{data?.all_total||0}} 条参与记录 · 点下面的卡片按结果筛<template v-if="data?.scan.pages"> · 已扫 {{data.scan.pages}} 页 / {{data.scan.scanned||0}} 条</template></span></div><div class="toolbar"><button class="tool-icon" :class="{on:searchOpen||!!query}" title="搜索活动奖品、UP 或动态号" aria-label="搜索" @click="toggleSearch"><Icon name="search" :size="16"/></button><button :class="{primary:filterCount>0}" @click="filtersOpen=true"><Icon name="filter" :size="16"/>筛选<span v-if="filterCount"> · {{filterCount}}</span></button><button @click="showDiscarded">移除记录</button><button :disabled="!!busy" @click="load">刷新</button><button :disabled="!!busy" @click="match(false)">{{busy==='match'?'匹配中…':'匹配本人转发'}}</button><button :disabled="!!busy||!data?.scan.cursor" @click="match(true)">继续匹配</button><button class="primary" :disabled="!!busy" @click="settle">{{busy==='settle'?'核验中…':'核验开奖'}}</button></div>
  <div v-if="searchOpen" class="search-strip"><input ref="searchInput" v-model="query" class="ipt" placeholder="活动奖品、UP 或动态号" aria-label="搜索参与记录" @keyup.enter="filter" @keyup.esc="closeSearch"/><button @click="filter"><Icon name="search" :size="16"/>搜索</button><button class="mini" @click="closeSearch">取消</button></div>
 </section>
 <p v-if="error" class="warning" role="alert">{{error}}</p>
 <div class="metric-row"><button v-for="(label,key) in outcomes" :key="key" class="panel metric-card" :class="[TONE[key],{selected:outcome===key}]" @click="outcome=outcome===key?'':key;filter()"><span>{{label}}</span><strong>{{data?.outcomes[key]||0}}</strong></button><button class="panel metric-card metric-tone-editor" @click="historyPage=0;showHistory()"><span>清理历史 ↗</span><strong>{{data?.cleanup.drop_share||0}}</strong><small>删转发 · {{data?.cleanup.unfollow||0}} 取关</small></button></div>
 <section class="panel list-panel result-list">
 <div class="list-scroll"><table class="dense w-full">
  <thead><tr><th class="cell-align-start">活动奖品</th><th>UP 主</th><th>开奖状态</th><th>本人转发</th><th class="cell-align-start">核验依据</th><th>操作</th></tr></thead>
  <tbody><tr v-for="row in data?.items||[]" :key="row.campaign_id">
   <td class="cell-align-start"><a :href="'https://t.bilibili.com/'+row.dyn_id" target="_blank" rel="noreferrer">{{row.reward||'奖品待补全'}}</a></td>
   <td><a v-if="row.up_uid" class="up-link" :href="'https://space.bilibili.com/'+row.up_uid" target="_blank" rel="noreferrer" :title="row.up_name">{{row.up_name||row.up_uid}}</a><span v-else class="muted">—</span></td>
   <td><span class="chip" :class="row.outcome==='won'?'chip-ok':row.outcome==='unknown'?'chip-warn':row.outcome==='void'?'chip-danger':''">{{outcomes[row.outcome]||'待确认'}}</span></td>
   <td><span class="chip link-chip" :class="'link-'+bucket(row)">{{links[bucket(row)]}}</span><a v-if="row.share_id" class="share-link" :href="'https://t.bilibili.com/'+row.share_id" target="_blank" rel="noreferrer">查看转发 ↗</a><small v-if="row.cleaned">转发已清理</small><small v-else-if="row.protected">已保留</small></td>
   <td class="cell-align-start evidence"><span class="evidence-text">{{row.check_error||row.evidence||'—'}}</span><small v-if="row.checked_at">{{new Date(row.checked_at).toLocaleDateString('zh-CN')}}</small></td>
   <td><div class="row-actions"><button v-if="Object.keys(row.choices).length" class="mini" @click="choose(row)">核对关联</button><button v-if="row.check_error==='缺少 UP 信息'" class="mini danger" :disabled="!!busy" @click="discardRow(row.campaign_id)">移除并删转发</button><ReviewEntry :dynamic="row.dyn_id" @saved="load"/></div></td>
  </tr></tbody>
 </table></div>
 <p v-if="!data?.items.length" class="empty-state">暂无记录</p><footer class="list-footer"><span>{{data?.total||0}} 条</span><button :disabled="!page" @click="page--;load()">上一页</button><span>{{page+1}} / {{Math.max(1,Math.ceil((data?.total||0)/50))}}</span><button :disabled="(page+1)*50>=(data?.total||0)" @click="page++;load()">下一页</button></footer></section>

 <Modal v-if="filtersOpen" title="筛选参与记录" @close="filtersOpen=false"><div class="filter-grid">
  <div class="filter-group"><h3>关联</h3><div class="compact-options"><button class="mini" :class="{on:!link}" @click="link='';filter()">全部关联</button><button v-for="(label,key) in links" :key="key" class="mini" :class="{on:link===key}" @click="link=link===key?'':key;filter()">{{label}} {{data?.links[key]||0}}</button></div></div>
  <div class="filter-group"><h3>结果</h3><div class="compact-options"><select class="ipt" v-model="outcome" aria-label="开奖结果" style="width:180px" @change="filter"><option value="">全部结果</option><option v-for="(label,key) in outcomes" :value="key" :key="key">{{label}}</option></select></div></div>
 </div><div class="modal-actions"><button @click="clearFilters">清空</button><button class="primary" @click="filtersOpen=false">显示 {{data?.total||0}} 条</button></div></Modal>

 <Modal v-if="discardOpen" title="核验失效项 · 移除记录" wide @close="discardOpen=false"><div class="table-scroll"><table class="dense w-full discard-table"><thead><tr><th>活动奖品</th><th>转发处理</th><th>操作</th></tr></thead><tbody><tr v-for="r in discarded" :key="r.campaign_id"><td>{{r.reward}}</td><td>{{r.note}}</td><td><button v-if="r.state!=='done'" :disabled="!!busy" @click="discardRow(r.campaign_id)">重查转发</button><span v-else class="chip chip-ok">已完成</span></td></tr></tbody></table></div><p v-if="!discarded.length" class="empty-state">暂无移除记录</p></Modal>
 <Modal v-if="historyOpen" title="清理历史" wide @close="historyOpen=false"><div class="table-scroll"><table class="dense w-full"><thead><tr><th>时间</th><th>操作</th><th>状态</th><th>活动奖品</th><th>记录</th></tr></thead><tbody><tr v-for="(r,i) in history" :key="i"><td>{{new Date(r.at!).toLocaleString('zh-CN')}}</td><td>{{r.action==='drop_share'?'删转发':'取关'}}</td><td>{{({done:'已完成',running:'开始执行',unknown:'待核对',failed:'失败'} as Record<string,string>)[r.state!]||r.state}}</td><td>{{r.reward||'奖品待补全'}}</td><td>{{r.note}}</td></tr></tbody></table></div><footer><button :disabled="!historyPage" @click="showHistory(-1)">上一页</button><button :disabled="history.length<50" @click="showHistory(1)">下一页</button></footer></Modal>
 <Modal v-if="chosen" title="核对本人转发" @close="chosen=undefined"><p class="chosen-title">{{chosen.reward||'奖品待补全'}}</p><label v-for="(_,id) in chosen.choices" :key="id" class="link-choice"><input type="radio" v-model="selected" :value="id"/><a :href="'https://t.bilibili.com/'+id" target="_blank" rel="noreferrer">{{id}} ↗</a></label><button class="primary" :disabled="!!busy||!selected" @click="saveLink">确认对应活动奖品</button></Modal>
</div></template>
<style scoped>
.results-workspace{display:flex;flex-direction:column;gap:20px;max-width:1400px;margin:auto}table{min-width:1020px}th:first-child{width:auto}th:nth-child(2){width:158px}th:nth-child(3){width:110px}th:nth-child(4){width:152px}th:nth-child(5){width:300px}th:last-child{width:130px}td{vertical-align:middle;white-space:normal;text-align:center}td a{color:var(--discover);text-decoration:none}td small,.share-link{display:block;margin-top:6px;font-size:var(--text-small);color:var(--muted)}.evidence{line-height:1.7;font-size:var(--text-meta);padding-inline:14px}.up-link{display:inline-block;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle}.row-actions{display:flex;gap:10px;flex-direction:column;align-items:center}.link-choice{display:flex;gap:12px;margin:18px 0}.chosen-title{font-weight:600}
.evidence-text{display:block;white-space:normal;overflow-wrap:anywhere;line-height:1.6}
td:first-child>a{font-weight:600;color:var(--text)}
/* 本人转发的六种状态各占一色：已核对绿、待核对蓝、多条候选黄、缺 UP 信息红、
   找不到粉、没扫灰。以前全是同一行灰字，一眼分不出哪条要人去看。 */
.link-chip{border-color:transparent}
.link-verified{color:var(--ok);background:var(--ok-soft)}
.link-candidate{color:var(--accent);background:var(--accent-soft)}
.link-conflict{color:var(--warn);background:var(--warn-soft)}
.link-blocked{color:var(--editor);background:color-mix(in srgb,var(--editor) 12%,transparent)}
.link-missing{color:var(--danger);background:var(--danger-soft)}
.link-unscanned{color:var(--muted);background:var(--surface-2)}
.discard-table{min-width:0}.discard-table th:first-child{width:44%}.discard-table th:nth-child(2){width:auto}.discard-table th:last-child{width:112px}.discard-table td{white-space:normal;overflow-wrap:anywhere;overflow:visible;text-overflow:clip;line-height:1.7}
</style>
