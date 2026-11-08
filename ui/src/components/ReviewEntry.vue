<script setup lang="ts">
import {ref,watch} from 'vue'
import {api,req,type CampaignRow} from '../api'
import {state,toast} from '../store'
import Modal from './Modal.vue'
const props=defineProps<{dynamic?:string;message?:string;reviewKey?:string;reviewed?:boolean}>()
const emit=defineEmits<{saved:[outcome:string]}>()
const open=ref(false),query=ref(''),rows=ref<CampaignRow[]>([]),chosen=ref<CampaignRow>(),outcome=ref('won'),busy=ref(false),owner=ref(0),name=ref(''),decision=ref('link')
async function search(){try{rows.value=(await api.campaigns({keyword:query.value,state:'review',limit:30})).items;chosen.value=props.dynamic?rows.value.find(r=>r.dyn_id===props.dynamic):undefined}catch(e){toast(String(e),'danger')}}
async function show(){const a=state.overview?.accounts.find(a=>a.current);owner.value=a?.id??0;name.value=a?.nickname??'';query.value=props.dynamic??'';chosen.value=undefined;rows.value=[];decision.value=props.message?(props.reviewed?'auto':'irrelevant'):'link';open.value=true;if(decision.value==='link')await search()}
watch(decision,v=>{if(v==='link'&&open.value)void search()})
async function save(){if(busy.value||decision.value==='link'&&!chosen.value)return;busy.value=true
 try{if(decision.value!=='link'){
  await req('/api/inbox/review',{method:'POST',body:JSON.stringify({account_id:owner.value,key:props.reviewKey,decision:decision.value})})
  toast(decision.value==='irrelevant'?'已标记与抽奖无关':'已恢复自动识别','ok')
 }else {await api.review(chosen.value!.id,outcome.value,props.message?'来自消息 '+props.message:'人工复核',owner.value,outcome.value==='won');if(props.reviewed&&props.reviewKey)await req('/api/inbox/review',{method:'POST',body:JSON.stringify({account_id:owner.value,key:props.reviewKey,decision:'auto'})})}
 open.value=false;emit('saved',decision.value==='link'?outcome.value:decision.value)
 }catch(e){toast(String(e),'danger')}finally{busy.value=false}}
</script>
<template><span><button class="mini" @click="show">{{message?'复核':'改判'}}</button>
<Modal v-if="open" :title="'复核 · '+name" @close="open=false" :can-close="()=>!busy">
<div class="review-fields">
<label v-if="message" class="field">这条消息<select class="ipt" v-model="decision"><option value="irrelevant">与抽奖无关</option><option value="link">关联活动奖品并改判</option><option v-if="reviewed" value="auto">恢复自动识别</option></select></label>
<template v-if="decision==='link'">
<label class="field">查找已参与的活动奖品<div class="search-controls"><input v-model="query" class="ipt" @keyup.enter="search"><button @click="search">查找</button></div></label>
<select v-model="chosen" class="ipt" aria-label="选择活动奖品"><option :value="undefined">选择活动奖品</option><option v-for="r in rows" :key="r.id" :value="r">{{r.reward||'奖品待补全'}} · {{r.dyn_id}}</option></select>
<select class="ipt" v-model="outcome" aria-label="奖品结论"><option value="won">中奖，并留存</option><option value="lost">未中奖</option><option value="void">作废</option></select>
</template>
<div class="modal-actions"><button :disabled="busy" @click="open=false">取消</button><button class="primary" :disabled="busy||decision==='link'&&!chosen||decision!=='link'&&!reviewKey" @click="save">{{busy?'保存中…':'保存'}}</button></div>
</div></Modal></span></template>
<style scoped>.review-fields{display:grid;gap:20px}.search-controls{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center}.search-controls input{min-width:0;width:100%}.field{display:grid;gap:10px}.mini{color:var(--accent)}</style>
