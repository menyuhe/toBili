<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {req} from '../api'
import Modal from './Modal.vue'
const props=defineProps<{account:{id:number;nickname:string}}>()
defineEmits<{close:[]}>()
type Group={id:number;name:string;count:number}
const groups=ref<Group[]>([]),uid=ref(''),result=ref(''),failure=ref(''),busy=ref(false),loaded=ref(false)
const url='/api/accounts/'+props.account.id+'/follow-groups'
async function read(check=false){
 if(busy.value)return
 busy.value=true;failure.value='';result.value=''
 try{
  if(check){
   const data=await req<{uid:string;result:string}>(url+'?uid='+encodeURIComponent(uid.value.trim()))
   result.value='UID '+data.uid+' · '+data.result
  }else{
   loaded.value=false
   groups.value=(await req<{groups:Group[]}>(url)).groups
   loaded.value=true
  }
 }catch(e){failure.value=String(e)}finally{busy.value=false}
}
onMounted(()=>void read())
</script>
<template><Modal :title="account.nickname+' · 关注分组'" @close="$emit('close')">
 <div class="group-panel">
  <div class="toolbar"><strong>关注分组</strong><button :disabled="busy" @click="read()">刷新</button></div>
  <div v-if="loaded" class="group-list"><span v-for="g in groups" :key="g.id" class="chip">{{g.name}} · {{g.count}}</span><span v-if="!groups.length">暂无分组</span></div>
  <span v-else-if="busy">正在读取…</span>
  <form class="toolbar" @submit.prevent="read(true)"><label>UP 主 UID <input class="ipt" inputmode="numeric" v-model.trim="uid" :disabled="busy" @input="result=''"/></label><button :disabled="busy||!/^[1-9][0-9]{0,19}$/.test(uid)">核对分组</button></form>
  <p v-if="result" role="status">{{result}}</p><p v-if="failure" role="alert" class="warning">{{failure}}</p>
 </div>
</Modal></template>
<style scoped>.group-panel{display:grid;gap:18px}.group-list{display:flex;flex-wrap:wrap;gap:8px}.group-panel .toolbar{flex-wrap:wrap;gap:8px}</style>
