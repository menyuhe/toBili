<script setup lang="ts">
/** 可折叠的分区。
 *
 *  为什么要有这个：这些页面每一块都是"偶尔才看一次"的配置（钉钉密钥、模型地址、
 *  清理策略），全部铺开时一屏放不下四块，人得滚三屏才能找到想改的那一项。
 *  收起来之后每个分区只留一行标题，需要的那块点开就行。
 *
 *  默认收起，但**已经配好的分区在标题右边带一个状态片**（"已设置""可用"），
 *  这样收起来也不会看不出它是什么状态 —— 收起不等于隐藏信息。
 */
withDefaults(
  defineProps<{
    title: string
    hint?: string
    open?: boolean
  }>(),
  { hint: '', open: false },
)

const shown = defineModel<boolean>('shown', { default: false })
</script>

<template>
  <section class="panel">
    <button type="button" class="fold-head" @click="shown = !shown">
      <svg
        class="chev"
        :class="{ 'chev-open': shown }"
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
      >
        <path d="m9 6 6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      <div class="min-w-0 flex-1 text-left">
        <div class="text-[14px] font-semibold truncate">{{ title }}</div>
        <div v-if="hint" class="text-[11.5px] muted truncate mt-0.5">{{ hint }}</div>
      </div>
      <slot name="right" />
    </button>
    <div v-show="shown" class="fold-body">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.fold-head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 11px 16px;
  background: transparent;
  border: none;
  color: var(--text);
  cursor: pointer;
  border-radius: 14px;
}
.fold-head:hover {
  background: var(--surface-2);
}
.chev {
  color: var(--muted);
  transition: transform 0.15s ease;
  flex: none;
}
.chev-open {
  transform: rotate(90deg);
}
.fold-body {
  border-top: 1px solid var(--line-soft);
}
</style>
