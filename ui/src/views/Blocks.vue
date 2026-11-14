<script setup lang="ts">
import BlockList from '../components/BlockList.vue'
</script>
<template>
  <section class="panel block-panel">
    <div class="panel-head"><div class="text-[14.5px] font-semibold">UP 黑名单</div><span class="text-[12px] muted">这些 UP 的活动不进列表，也不会被自动参与</span></div>
    <div class="block-body"><BlockList /></div>
  </section>
</template>
<style scoped>
.block-panel{max-width:1100px;margin:auto}
.block-body{padding:16px 18px}
</style>
