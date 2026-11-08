<script setup lang="ts">
import {ref} from 'vue'
import {req} from '../api'
import {toast} from '../store'
import Modal from './Modal.vue'
const props=defineProps<{id:number}>()
const emit=defineEmits<{saved:[]}>()
const opened=ref(false),busy=ref(false),reward=ref(''),expected=ref(''),note=ref(''),dyn=ref(''),error=ref('')
async function open(){busy.value=true;try{const {campaign:c}=await req<{campaign:Record<string,any>}>(`/api/campaigns/${props.id}`);reward.value=expected.value=c.reward||'';note.value=c.reward_evidence||'';dyn.value=c.dyn_id;error.value='';opened.value=true}catch(e){toast(String(e),'danger')}finally{busy.value=false}}
async function save(){busy.value=true;error.value='';try{await req(`/api/campaigns/${props.id}/prize`,{method:'PUT',body:JSON.stringify({reward:reward.value,expected:expected.value,evidence:note.value})});opened.value=false;emit('saved');toast('奖品已保存','ok')}catch(e){error.value=String(e)}finally{busy.value=false}}
</script>
<template><button class="mini edit-prize" :disabled="busy" @click.stop.prevent="open">修改奖品</button><Modal v-if="opened" title="修改活动奖品" :can-close="()=>!busy" @close="opened=false"><form @submit.prevent="save" class="prize-form"><a :href="'https://t.bilibili.com/'+dyn" target="_blank" rel="noreferrer">查看原动态 ↗</a><label class="field">具体奖品<textarea class="ipt" v-model="reward" rows="4" maxlength="500" required autofocus/></label><label class="field">依据 / 备注<input class="ipt" v-model="note" maxlength="2000" placeholder="例如：第 2 张图，或视频 02:35"/></label><p v-if="error" class="warning">{{error}}</p><div class="toolbar"><button type="button" :disabled="busy" @click="opened=false">取消</button><button class="primary" :disabled="busy||!reward.trim()">{{busy?'保存中…':'保存'}}</button></div></form></Modal></template>
<style scoped>.prize-form{display:grid;gap:20px}.prize-form .toolbar{justify-content:flex-end}.prize-form>a{color:var(--discover);overflow-wrap:anywhere}.edit-prize{background:transparent;color:var(--editor);border-color:transparent}</style>
