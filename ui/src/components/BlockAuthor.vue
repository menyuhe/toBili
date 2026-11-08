<script setup lang="ts">
import IconButton from "./IconButton.vue"
import {ref} from 'vue'
import {req} from '../api'
import {toast,refreshQueue} from '../store'
import Modal from './Modal.vue'
const props=defineProps<{uid:string;name:string}>(),emit=defineEmits<{saved:[]}>()
const opened=ref(false),busy=ref(false),reason=ref(''),error=ref('')
async function save(){busy.value=true;try{const r=await req<{synced:boolean;pending?:boolean}>('/api/blocks',{method:'PUT',body:JSON.stringify({uid:props.uid,name:props.name,reason:reason.value,active:true})});opened.value=false;void refreshQueue();emit('saved');toast(r.pending?'已在本机拦截，等待同步':'已加入 UP 黑名单',r.pending?'info':'ok')}catch(e){error.value=String(e)}finally{busy.value=false}}
</script>
<template><IconButton v-if="uid" icon="ban" :label="'屏蔽 UP：'+(name||uid)" tone="warn" small @click.stop.prevent="opened=true"/><Modal v-if="opened" title="加入 UP 黑名单" :can-close="()=>!busy" @close="opened=false"><form @submit.prevent="save" class="block-form"><strong>{{name||uid}}</strong><span class="muted">UID {{uid}} · 所有账号禁止参与</span><label class="field">原因<input class="ipt" v-model="reason" maxlength="500" placeholder="可不填"/></label><p v-if="error" class="warning">{{error}}</p><div class="toolbar"><button type="button" :disabled="busy" @click="opened=false">取消</button><button class="primary" :disabled="busy">加入黑名单</button></div></form></Modal></template>
<style scoped>.block-form{display:grid;gap:var(--space-5)}.block-form .toolbar{justify-content:flex-end}</style>
