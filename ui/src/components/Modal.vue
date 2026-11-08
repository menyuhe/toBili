<script setup lang="ts">
import {onMounted,ref} from 'vue'
import IconButton from './IconButton.vue'
const props=defineProps<{title:string;wide?:boolean;canClose?:()=>boolean|Promise<boolean>}>()
const emit=defineEmits<{close:[]}>()
const dialog=ref<HTMLDialogElement>()
async function close(){if(!props.canClose||await props.canClose()){dialog.value?.close();emit('close')}}
onMounted(()=>dialog.value?.showModal())
</script>
<template><Teleport to="body"><dialog ref="dialog" class="app-modal" :class="{'modal-wide':wide}" @cancel.prevent="close"><header><h2>{{title}}</h2><IconButton icon="close" label="关闭" @click="close"/></header><div class="modal-body"><slot/></div></dialog></Teleport></template>
