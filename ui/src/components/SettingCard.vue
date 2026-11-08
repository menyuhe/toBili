<script setup lang="ts">
import Modal from './Modal.vue'
defineProps<{shown:boolean;title:string}>()
const emit=defineEmits<{'update:shown':[value:boolean]}>()
</script>
<template><section class="panel setting-card"><div class="setting-top"><h2>{{title}}</h2><slot name="right"/></div><button class="btn" @click="emit('update:shown',true)">配置</button><Modal v-if="shown" :title="title" @close="emit('update:shown',false)"><slot/></Modal></section></template>
<style scoped>.setting-card{padding:24px;display:flex;flex-direction:column;gap:28px;border-top:3px solid var(--accent)}.setting-card:nth-child(2){border-top-color:var(--discover)}.setting-card:nth-child(3){border-top-color:var(--editor)}.setting-card:nth-child(4){border-top-color:var(--warn)}.setting-top{display:flex;align-items:center;justify-content:space-between;gap:12px}.setting-top h2{font-size:17px;font-weight:650}.setting-card>button{align-self:flex-start}</style>
