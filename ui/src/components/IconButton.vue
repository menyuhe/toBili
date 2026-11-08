<script setup lang="ts">
import Icon from './Icon.vue'
withDefaults(defineProps<{icon:string;label:string;tone?:'neutral'|'warn'|'danger';small?:boolean}>(),{tone:'neutral',small:false})
</script>
<template><button type="button" class="icon-button" :class="['tone-'+tone,{'icon-button-small':small}]" :title="label" :aria-label="label"><Icon :name="icon" :size="16"/></button></template>
