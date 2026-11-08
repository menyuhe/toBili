<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {req} from '../api'
import {toast,refreshGuard} from '../store'
const seconds=ref(15),owner=ref(0),busy=ref(false),ready=ref(false)
onMounted(async()=>{try{const p=await req<{account_id:number;interval_seconds:number}>('/api/settings/pace');owner.value=p.account_id;seconds.value=p.interval_seconds;ready.value=true}catch(e){toast(String(e),'danger')}})
async function save(){busy.value=true;try{await req('/api/settings/pace',{method:'PUT',body:JSON.stringify({account_id:owner.value,interval_seconds:seconds.value})});toast('已保存，下次开始参与时生效','ok');await refreshGuard()}catch(e){toast(String(e),'danger')}finally{busy.value=false}}
</script>
<template><form class="pace-form" @submit.prevent="save"><label for="participation-seconds">活动之间的间隔（秒）</label><input id="participation-seconds" v-model.number="seconds" type="number" min="0" max="3600" step="0.1" required :disabled="!ready"><div class="toolbar"><button v-for="n in [0,0.8,5,15]" :key="n" type="button" :aria-pressed="seconds===n" @click="seconds=n">{{n}} 秒</button></div><button class="primary" :disabled="busy||!ready" type="submit">保存</button></form></template>
<style scoped>.pace-form{display:flex;flex-direction:column;align-items:flex-start;gap:18px;padding:8px}.pace-form input{width:100%;min-height:40px}.pace-form label{font-weight:600}.pace-form button[aria-pressed=true]{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}</style>
