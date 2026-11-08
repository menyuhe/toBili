<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {req} from '../api'
import {toast} from '../store'
import Modal from '../components/Modal.vue'
type Row={uid:string;name:string;reason:string;sync_state:string}
const rows=ref<Row[]>([]),shared=ref(false),busy=ref(false),adding=ref(false),uid=ref(''),name=ref(''),reason=ref('')
async function load(){try{const r=await req<{items:Row[];shared:boolean}>('/api/blocks');rows.value=r.items;shared.value=r.shared}catch(e){toast(String(e),'danger')}}
async function save(row:Row|undefined,active:boolean){if(row&&!confirm(`从黑名单移除 ${row.name||row.uid}？`))return;busy.value=true;try{const r=await req<{pending?:boolean}>('/api/blocks',{method:'PUT',body:JSON.stringify({uid:row?.uid||uid.value,name:row?.name||name.value,reason:row?.reason||reason.value,active})});adding.value=false;await load();toast(r.pending?'已在本机拦截，等待同步':'已保存',r.pending?'info':'ok')}catch(e){toast(String(e),'danger')}finally{busy.value=false}}
onMounted(load)
</script>
<template><section class="panel"><div class="panel-head toolbar"><h2>UP 黑名单 · {{rows.length}}</h2><button @click="adding=true">添加 UID</button><button @click="load">刷新</button></div><div class="block-table"><table class="dense"><thead><tr><th>UP</th><th>UID</th><th>原因</th><th>{{shared?'同步状态':'状态'}}</th><th>操作</th></tr></thead><tbody><tr v-for="r in rows" :key="r.uid"><td>{{r.name||'—'}}</td><td><a :href="'https://space.bilibili.com/'+r.uid" target="_blank" rel="noreferrer">{{r.uid}} ↗</a></td><td>{{r.reason||'—'}}</td><td><span :class="r.sync_state==='pending'?'warning':'muted'">{{r.sync_state==='pending'?'等待同步':shared?'已同步':'本机生效'}}</span></td><td><button :disabled="busy" @click="save(r,false)">移除</button></td></tr><tr v-if="!rows.length"><td colspan="5" class="empty-state">暂无黑名单</td></tr></tbody></table></div><Modal v-if="adding" title="加入 UP 黑名单" :can-close="()=>!busy" @close="adding=false"><form @submit.prevent="save(undefined,true)" class="block-fields"><label class="field">UP UID<input class="ipt" v-model="uid" pattern="[0-9]{1,24}" required/></label><label class="field">UP 名称<input class="ipt" v-model="name" maxlength="120"/></label><label class="field">原因<input class="ipt" v-model="reason" maxlength="500"/></label><button class="primary" :disabled="busy">加入黑名单</button></form></Modal></section></template>
<style scoped>.block-table{overflow:auto}.block-table table{width:100%;min-width:700px}.block-table td{text-align:center;white-space:normal}.block-fields{display:grid;gap:18px}</style>
