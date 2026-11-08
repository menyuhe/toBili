<script setup lang="ts">
import {ref,onUnmounted} from 'vue'
import {req} from '../api'
import {currentAccount,toast} from '../store'
const props=defineProps<{url:string;label?:string}>()
const busy=ref(false)
let alive=true
onUnmounted(()=>alive=false)
async function open(){if(busy.value)return;busy.value=true;const account_id=currentAccount()
 try{
  const {ticket}=await req<{ticket:string}>('/api/account-browser',{method:'POST',body:JSON.stringify({account_id,url:props.url})})
  for(let i=0;i<90&&alive;i++){
   const r=await req<{state:string;note:string}>('/api/account-browser/'+ticket)
   if(r.state==='ready'){toast('已打开该账号的 B 站窗口','ok');return}
   if(r.state==='error')throw new Error(r.note)
   if(r.state==='closed')return
   await new Promise(resolve=>setTimeout(resolve,500))
  }
  if(alive)toast('账号窗口仍在启动，请稍后查看','warn')
 }catch(e){if(alive)toast(String(e),'danger')}finally{busy.value=false}}
</script>
<template><button class="account-browser" :disabled="busy" @click="open">{{busy?'正在打开…':label||'在 B 站打开'}}</button></template>
<style scoped>.account-browser{color:var(--discover)}.account-browser:hover:not(:disabled){background:var(--discover-soft)}</style>
