<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {req} from '../api'
import {toast} from '../store'
import Modal from './Modal.vue'
type Row={uid:string;name:string;reason:string;sync_state:string}
const rows=ref<Row[]>([]),shared=ref(false),busy=ref(false),adding=ref(false),uid=ref(''),name=ref(''),reason=ref('')
const emit=defineEmits<{count:[number]}>()
async function load(){try{const r=await req<{items:Row[];shared:boolean}>('/api/blocks');rows.value=r.items;shared.value=r.shared;emit('count',r.items.length)}catch(e){toast(String(e),'danger')}}
async function save(row:Row|undefined,active:boolean){if(row&&!confirm(`从黑名单移除 ${row.name||row.uid}？`))return;busy.value=true;try{const r=await req<{pending?:boolean}>('/api/blocks',{method:'PUT',body:JSON.stringify({uid:row?.uid||uid.value,name:row?.name||name.value,reason:row?.reason||reason.value,active})});adding.value=false;await load();toast(r.pending?'已在本机拦截，等待同步':'已保存',r.pending?'info':'ok')}catch(e){toast(String(e),'danger')}finally{busy.value=false}}
function openAdd(){uid.value='';name.value='';reason.value='';adding.value=true}
onMounted(load)
</script>
<template>
<div class="block-list">
  <div class="block-toolbar">
    <span class="text-[13px] muted">{{rows.length}} 个 UP · {{shared?'已同步到账号':'仅本机生效'}}</span>
    <div class="toolbar"><button class="mini" @click="load">刷新</button><button class="mini primary" @click="openAdd">添加 UID</button></div>
  </div>
  <div class="table-scroll">
    <table class="dense block-table">
      <thead><tr><th class="cell-align-start">UP</th><th>UID</th><th class="cell-align-start">原因</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.uid">
          <td class="cell-align-start">{{r.name||'—'}}</td>
          <td><a :href="'https://space.bilibili.com/'+r.uid" target="_blank" rel="noreferrer">{{r.uid}} ↗</a></td>
          <td class="cell-align-start">{{r.reason||'—'}}</td>
          <td><span class="chip" :class="r.sync_state==='pending'?'chip-warn':'chip-ok'">{{r.sync_state==='pending'?'等待同步':shared?'已同步':'本机生效'}}</span></td>
          <td><button class="mini danger" :disabled="busy" @click="save(r,false)">移除</button></td>
        </tr>
        <tr v-if="!rows.length"><td colspan="5" class="empty-state">暂无黑名单</td></tr>
      </tbody>
    </table>
  </div>
  <Modal v-if="adding" title="加入 UP 黑名单" :can-close="()=>!busy" @close="adding=false">
    <form @submit.prevent="save(undefined,true)" class="block-fields">
      <label class="field">UP UID<input class="ipt" v-model="uid" pattern="[0-9]{1,24}" required/></label>
      <label class="field">UP 名称<input class="ipt" v-model="name" maxlength="120"/></label>
      <label class="field">原因<input class="ipt" v-model="reason" maxlength="500"/></label>
      <div class="modal-actions"><button type="button" class="btn" @click="adding=false">取消</button><button class="primary" :disabled="busy">加入黑名单</button></div>
    </form>
  </Modal>
</div>
</template>
<style scoped>
.block-list{display:flex;flex-direction:column;min-height:0}
.block-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:0 0 12px}
.table-scroll{overflow:auto;max-height:56vh;border:1px solid var(--line-soft);border-radius:var(--radius-control)}
.block-table{width:100%;min-width:640px}
.block-table td{white-space:normal;overflow:visible}
.block-table tbody tr:hover{background:var(--surface-2)}
.block-table a{color:var(--discover);white-space:nowrap}
.block-table td:nth-child(2){white-space:nowrap}
.block-fields{display:grid;gap:16px}
</style>
