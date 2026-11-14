<script setup lang="ts">
import {computed,onUnmounted,ref,watch} from 'vue'
import {req,type AccountRow,type AccountSession} from '../api'
import {refreshOverview} from '../store'
const props=defineProps<{account:AccountRow}>()
const emit=defineEmits<{login:[]}>()
const local=ref<AccountSession>(),busy=ref(false),error=ref('')
let alive=true
const session=computed(()=>local.value||props.account.session)
const label=computed(()=>({unknown:'未检查',checking:'检查中',valid:'登录正常',unavailable:'暂无法确认',login_required:'需重新登录'}[session.value?.state||'unknown']))
watch(()=>props.account.session,v=>{local.value=v})
async function check(){
 if(busy.value)return
 busy.value=true;error.value=''
 try{
  local.value=await req<AccountSession>(`/api/accounts/${props.account.id}/check`,{method:'POST',body:JSON.stringify({force:true})})
  for(let n=0;n<15&&alive&&local.value?.checking;n++){
   await new Promise(resolve=>setTimeout(resolve,1000));if(!alive)return
   const data=await req<{items:AccountRow[]}>('/api/accounts')
   local.value=data.items.find(r=>r.id===props.account.id)?.session
  }
  if(alive)await refreshOverview()
 }catch(e){if(alive)error.value=String(e)}finally{if(alive)busy.value=false}
}
onUnmounted(()=>{alive=false})
</script>
<template><div class="account-session" @click.stop>
 <div class="session-line"><span class="chip" :class="session?.state==='valid'?'chip-ok':session?.state==='login_required'?'chip-danger':'chip-warn'" :title="session?.note">{{label}}</span><button :disabled="busy" @click="check">{{busy?'检查中…':'检查登录'}}</button><button v-if="session?.state==='login_required'||!account.ready" @click="emit('login')">重新扫码</button></div>
 <small v-if="error||session?.state==='unavailable'||session?.state==='login_required'" role="status">{{error||session?.note}}</small>
</div></template>
<style scoped>
.account-session{display:grid;gap:5px;min-width:0}.session-line{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.session-line button{font-size:var(--text-small);height:26px;padding:0 7px}.account-session small{font-size:var(--text-small);color:var(--muted);line-height:1.5;overflow-wrap:anywhere}
</style>
