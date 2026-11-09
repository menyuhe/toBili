<script setup lang="ts">
import {ref} from 'vue'
import {req} from '../api'
import {toast} from '../store'
import Modal from './Modal.vue'
import IconButton from './IconButton.vue'
import DateField from './DateField.vue'
const props=defineProps<{id:number}>(),emit=defineEmits<{saved:[]}>()
const opened=ref(false),busy=ref(false),value=ref(''),expected=ref<string|null>(null),error=ref('')
async function open(){busy.value=true;try{const {campaign:c}=await req<{campaign:{deadline_at:string|null}}>(`/api/campaigns/${props.id}`);expected.value=c.deadline_at;value.value=c.deadline_at?new Date(new Date(c.deadline_at).getTime()+8*3600000).toISOString().slice(0,16):'';error.value='';opened.value=true}catch(e){toast(String(e),'danger')}finally{busy.value=false}}
async function save(){busy.value=true;try{await req(`/api/campaigns/${props.id}/deadline`,{method:'PUT',body:JSON.stringify({value:value.value+':00+08:00',expected:expected.value})});opened.value=false;emit('saved');toast('开奖时间已保存','ok')}catch(e){error.value=String(e)}finally{busy.value=false}}
</script>
<template><IconButton small icon="pen" label="修改开奖时间" :disabled="busy" @click="open"/><Modal v-if="opened" title="修改开奖时间" :can-close="()=>!busy" @close="opened=false"><form class="deadline-form" @submit.prevent="save"><label>开奖时间（北京时间）</label><DateField v-model="value" time label="开奖时间"/><p v-if="error" class="warning">{{error}}</p><div class="modal-actions"><button type="button" @click="opened=false">取消</button><button class="primary" :disabled="busy||!value">保存</button></div></form></Modal></template>
<style scoped>.deadline-form{display:grid;gap:16px}</style>
