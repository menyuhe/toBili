<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
const props = withDefaults(defineProps<{ src?: string | null; name: string; size?: number }>(), { size: 32 })
const broken = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let retries = 0
function failed() {
  broken.value = true
  if (retries++ < 2) timer = setTimeout(() => { broken.value = false }, 65000)
}
watch(() => [props.src, props.name], () => {
  clearTimeout(timer); broken.value = false; retries = 0
})
onUnmounted(() => clearTimeout(timer))
</script>
<template>
  <span class="account-avatar-image" :style="{ width: size + 'px', height: size + 'px' }" :title="name">
    <span aria-hidden="true">{{ name.slice(0, 1) || '?' }}</span>
    <img v-if="src && !broken" :src="src" alt="" referrerpolicy="no-referrer" @error="failed" />
  </span>
</template>
<style scoped>
.account-avatar-image{position:relative;display:inline-grid;place-items:center;flex:none;overflow:hidden;border-radius:50%;background:var(--accent-soft);color:var(--accent);border:1px solid var(--line);font-size:14px;font-weight:600;line-height:1}
.account-avatar-image img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
</style>
