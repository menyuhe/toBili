<script setup lang="ts">
/** 扫码登录的二维码本体。
 *
 *  从 LoginPanel 里抽出来，因为"添加一个账号"要的是同一块东西 —— 复制一份的话，
 *  以后改二维码尺寸、改轮询间隔就得改两个地方，而漏改的那个不会报错，
 *  只会在某天扫码时静默失效。
 */
import { onMounted, onUnmounted, ref } from 'vue'
import QRCode from 'qrcode'

import { api, type Profile } from '../api'
import { refreshAll, toast } from '../store'

const props = defineProps<{ size?: number }>()
const emit = defineEmits<{ done: [Profile | undefined] }>()

const canvas = ref<HTMLCanvasElement | null>(null)
const phase = ref<'loading' | 'waiting' | 'scanned' | 'expired' | 'error'>('loading')
const tip = ref('')
const key = ref('')
let poller: number | undefined
let busy = false

const side = props.size ?? 204

async function issue() {
  phase.value = 'loading'
  tip.value = ''
  try {
    const ticket = await api.qr()
    key.value = ticket.key
    if (canvas.value) {
      // 白底是必须的：深色背景上的浅色二维码，很多手机扫不出来。
      await QRCode.toCanvas(canvas.value, ticket.url, {
        width: side,
        margin: 1,
        // 'medium' 就是 QR 规范里的 M 级（15% 纠错）。写成 'M' 运行时也对，
        // 但 qrcode 的类型声明只认长名，构建时会把这条当错误报出来。
        errorCorrectionLevel: 'medium',
        color: { dark: '#0a0c10', light: '#ffffff' },
      })
    }
    phase.value = 'waiting'
    poll()
  } catch (error) {
    phase.value = 'error'
    tip.value = error instanceof Error ? error.message : '拿不到二维码'
  }
}

function poll() {
  window.clearInterval(poller)
  poller = window.setInterval(async () => {
    if (busy || !key.value) return
    busy = true
    try {
      const result = await api.pollQr(key.value)
      if (result.state === 'scanned') {
        phase.value = 'scanned'
      } else if (result.state === 'expired' || result.state === 'rejected') {
        phase.value = result.state === 'expired' ? 'expired' : 'error'
        tip.value =
          result.state === 'expired' ? '二维码过期了，点下面换一张' : '这次扫码被拒绝，重试一次'
        window.clearInterval(poller)
      } else if (result.state === 'confirmed') {
        window.clearInterval(poller)
        toast(`欢迎回来，${result.account?.nickname ?? ''}`, 'ok')
        await refreshAll()
        emit('done', result.account)
      }
    } catch {
      // 网络抖一下就重试下一轮，不打断扫码节奏
    } finally {
      busy = false
    }
  }, 2000)
}

onMounted(() => void issue())
onUnmounted(() => window.clearInterval(poller))
</script>

<template>
  <div
    class="relative rounded-xl overflow-hidden bg-white p-2"
    :style="{ width: `${side + 16}px`, height: `${side + 16}px` }"
  >
    <canvas ref="canvas" class="block" />

    <div
      v-if="phase !== 'waiting' && phase !== 'scanned'"
      class="absolute inset-0 grid place-items-center text-center px-4"
      style="background: rgba(10, 12, 16, 0.88); backdrop-filter: blur(3px)"
    >
      <div>
        <div v-if="phase === 'loading'" class="text-white text-[13.5px] live">正在取二维码…</div>
        <template v-else-if="phase === 'expired'">
          <div class="text-white text-[13.5px] mb-3">二维码已过期</div>
          <button class="btn-primary text-[13.5px]" @click="issue">换一张</button>
        </template>
        <template v-else>
          <div class="text-white text-[13.5px] mb-2 max-w-[180px] leading-relaxed">{{ tip }}</div>
          <button class="btn-primary text-[13.5px]" @click="issue">重试</button>
        </template>
      </div>
    </div>

    <div
      v-else-if="phase === 'scanned'"
      class="absolute inset-0 grid place-items-center text-center px-4"
      style="background: rgba(10, 12, 16, 0.9); backdrop-filter: blur(3px)"
    >
      <div class="text-white text-[13.5px] leading-relaxed">
        已扫码<br />
        <span class="opacity-70 text-[12.5px]">请在手机上点确认</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.btn-primary {
  height: 30px;
  padding: 0 14px;
  border-radius:var(--radius-control);
  border: none;
  background: var(--accent);
  color: #0a0c10;
  font-weight: 600;
  cursor: pointer;
}
.btn-primary:hover {
  filter: brightness(1.06);
}
</style>
