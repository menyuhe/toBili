<script setup lang="ts">
/** 整屏扫码页。二维码本体在 QrLogin 里，"添加账号"弹窗复用同一份。 */
import { state } from '../store'
import QrLogin from './QrLogin.vue'
</script>

<template>
  <div class="h-[100dvh] w-screen grid place-items-center relative overflow-hidden">
    <!-- 背景的暖色光斑，纯装饰。它让空荡荡的登录页不至于像报错页。 -->
    <div class="glow" />

    <div class="relative flex flex-col items-center enter">
      <div class="flex items-center gap-3 mb-8">
        <img src="/icon.png" alt="toBili" class="w-11 h-11 flex-none" />
        <div>
          <div class="text-[22px] font-semibold tracking-tight leading-none">toBili</div>
          <div class="text-[12.5px] muted mt-1.5">B 站抽奖活动管理</div>
        </div>
      </div>

      <div class="panel w-[360px] p-7 flex flex-col items-center">
        <QrLogin />

        <p class="mt-5 text-[13.5px] dim text-center leading-relaxed">
          用 B 站手机客户端扫码登录<br />
          <span class="muted text-[12.5px]">凭据只存在本机，用系统级加密保存</span>
        </p>

      </div>

      <div class="mt-5 flex items-center gap-2 text-[12px] muted">
        <span
          class="inline-block w-1.5 h-1.5 rounded-full"
          :style="{ background: state.connected ? 'var(--ok)' : 'var(--muted)' }"
        />
        {{ state.connected ? '已连接本地服务' : '正在连接本地服务' }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.glow {
  position: absolute;
  width: 720px;
  height: 720px;
  border-radius: 50%;
  background: radial-gradient(circle, var(--accent-soft) 0%, transparent 62%);
  filter: blur(30px);
  top: -240px;
  left: 50%;
  transform: translateX(-50%);
  pointer-events: none;
}
</style>
