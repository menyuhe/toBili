<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
const props=defineProps<{at:number}>()
const now=ref(Date.now())
const text=computed(()=>{
 const seconds=Math.ceil((props.at*1000-now.value)/1000)
 if(!Number.isFinite(seconds))return '未排期'
 if(seconds<=0)return '等待执行'
 const h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),s=seconds%60
 return [h,m,s].map(n=>String(n).padStart(2,'0')).join(':')
})
let timer:ReturnType<typeof setInterval>|undefined
onMounted(()=>{timer=setInterval(()=>now.value=Date.now(),1000)})
onUnmounted(()=>{if(timer)clearInterval(timer)})
</script>
<template><span class="countdown" aria-label="距离下次定时任务">{{text}}</span></template>
<style scoped>.countdown{font-variant-numeric:tabular-nums;white-space:nowrap;font-weight:600}</style>
