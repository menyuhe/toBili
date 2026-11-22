<script setup lang="ts">
import {ref} from 'vue'
import {req} from '../api'
import {refreshGuard,toast} from '../store'
export type CleanupState={paused:boolean;reason:string;earliest:number;waiting:string;backlog:string}
const props=defineProps<{status:CleanupState;owner:number;current:boolean;next?:number}>()
const emit=defineEmits<{updated:[]}>()
const busy=ref(false)
async function resume(){
 if(!props.current||busy.value)return
 busy.value=true
 try{
  const result=await req<{open:boolean;reason:string}>('/api/tidy/resume?account_id='+props.owner,{method:'POST'})
  toast(result.open?'清理已恢复，将按排期检查':result.reason,result.open?'ok':'warn')
  emit('updated');void refreshGuard()
 }catch(e){toast(String(e),'danger')}finally{busy.value=false}
}
</script>
<template>
 <div v-if="status.paused" class="cleanup-state">
  <strong>清理已暂停</strong><span>{{status.reason}}</span>
  <details><summary>查看原因</summary>此账号的清理排期已暂停。恢复前会重新检查风控和未读消息。</details>
  <button v-if="current" :disabled="busy" @click="resume">{{busy?'正在检查…':'恢复清理'}}</button>
 </div>
 <p v-else-if="next" class="dim"><template v-if="status.waiting">{{status.waiting}}，最早 {{new Date(Math.max(next,status.earliest)*1000).toLocaleTimeString('zh-CN',{hour12:false})}} 可清理。 </template><template v-if="status.backlog!==''">上次核验待清理 {{status.backlog}} 条</template></p>
</template>
<style scoped>.cleanup-state{display:flex;align-items:center;flex-wrap:wrap;gap:8px;color:var(--warn);font-size:var(--text-meta)}.cleanup-state details{color:var(--text-dim)}</style>
