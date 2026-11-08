<script setup lang="ts">
import {computed,ref} from 'vue'
import Icon from './Icon.vue'
const props=defineProps<{modelValue:string;label:string;time?:boolean}>()
const emit=defineEmits<{'update:modelValue':[value:string];change:[]}>()
const picker=ref<HTMLInputElement>(),text=ref<HTMLInputElement>()
const display=computed(()=>props.modelValue.replace('T',' ').replaceAll('-','/'))
function set(value:string){emit('update:modelValue',value);emit('change')}
function commit(event:Event){const input=event.target as HTMLInputElement;const s=input.value.trim();if(!s){input.setCustomValidity('');set('');return}
const match=s.match(/^(\d{4})[年/\-](\d{1,2})[月/\-](\d{1,2})日?(?:[ T]+(\d{1,2})[:：](\d{2}))?$/)
if(match){const [_,y,m,d,h='00',n='00']=match;const date=new Date(+y!,+m!-1,+d!,+h,+n);if(date.getFullYear()===+y!&&date.getMonth()===+m!-1&&date.getDate()===+d!&&+h<24&&+n<60){input.setCustomValidity('');set(`${y}-${m!.padStart(2,'0')}-${d!.padStart(2,'0')}${props.time?'T'+h.padStart(2,'0')+':'+n:''}`);return}}
input.setCustomValidity(props.time?'请输入有效日期和时间，例如 2026/11/08 18:30':'请输入有效日期，例如 2026/11/08');input.reportValidity()}
function open(){try{picker.value?.showPicker()}catch{text.value?.focus()}}
</script>
<template><span class="date-field"><input ref="text" type="text" :value="display" :aria-label="label" :placeholder="time?'年/月/日 时:分':'年 / 月 / 日'" @input="($event.target as HTMLInputElement).setCustomValidity('')" @change="commit"/><button type="button" :aria-label="'选择'+label" @click="open"><Icon name="calendar" :size="16"/></button><input ref="picker" class="native-date" :type="time?'datetime-local':'date'" :value="modelValue" tabindex="-1" aria-hidden="true" @change="set(($event.target as HTMLInputElement).value)"/></span></template>
<style scoped>.date-field{position:relative;display:flex;align-items:center;min-width:0;gap:4px;border:1px solid var(--line);border-radius:var(--radius-control);background:var(--surface-2);padding-right:4px}.date-field>input[type=text]{width:100%;min-width:0;border:0;background:transparent;box-shadow:none;padding:9px 10px;color:var(--text);font:inherit;font-size:var(--text-meta)}.date-field>button{width:28px;height:28px;min-height:0;flex:none;padding:0;display:grid;place-items:center;border:0;background:transparent;color:var(--muted)}.native-date{position:absolute;right:0;bottom:0;width:1px;height:1px;padding:0;opacity:0;pointer-events:none}</style>
