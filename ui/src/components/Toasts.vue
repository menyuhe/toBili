<script setup lang="ts">
import { state } from '../store'

defineEmits<{ dismiss: [id: number] }>()

const tone = {
  ok: { color: 'var(--ok)', bg: 'var(--ok-soft)', icon: 'M5 13l4 4L19 7' },
  warn: { color: 'var(--warn)', bg: 'var(--warn-soft)', icon: 'M12 8v5m0 3v.5' },
  danger: { color: 'var(--danger)', bg: 'var(--danger-soft)', icon: 'M12 8v6m0 3v.5' },
  info: { color: 'var(--text-dim)', bg: 'var(--surface-3)', icon: 'M12 11v6m0-9v.5' },
} as const
</script>

<template>
  <div class="fixed bottom-5 right-5 z-[9999] flex flex-col gap-2 items-end pointer-events-none">
    <TransitionGroup name="toast">
      <div
        v-for="item in state.toasts"
        :key="item.id"
        class="pointer-events-auto flex items-start gap-2.5 max-w-[400px] pl-3 pr-2 py-2.5 rounded-xl border text-[13.5px] leading-relaxed"
        :style="{
          background: 'var(--surface-3)',
          borderColor: 'var(--line)',
          boxShadow: 'var(--shadow)',
          color: 'var(--text)',
        }"
      >
        <span
          class="flex-none w-5 h-5 rounded-full grid place-items-center mt-0.5"
          :style="{ background: tone[item.kind].bg, color: tone[item.kind].color }"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
            <path :d="tone[item.kind].icon" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" />
          </svg>
        </span>
        <span class="flex-1 py-0.5">{{ item.text }}</span>
        <button
          class="toast-close"
          aria-label="关闭提示"
          @click="$emit('dismiss', item.id)"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
            <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-close{flex:none;display:grid;place-items:center;width:26px;height:26px;min-height:0;padding:0;border:0;border-radius:50%;background:transparent;color:var(--muted);align-self:center}
.toast-close:hover:not(:disabled){background:var(--surface-2);color:var(--text)}
.toast-close svg{display:block;width:14px;height:14px}
.toast-enter-active,
.toast-leave-active {
  transition: all 0.24s cubic-bezier(0.22, 1, 0.36, 1);
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(16px) scale(0.97);
}
</style>
