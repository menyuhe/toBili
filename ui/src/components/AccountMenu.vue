<script setup lang="ts">
/** 顶栏的账号切换器。
 *
 *  三件事挤在一个下拉里：切号、摘号、加号。摘号单独放在每个账号的右侧而不是
 *  底部统一入口 —— "删哪个"必须是点到哪个就删哪个，不能选。
 *
 *  切号之后整页数据都要换一套（订阅源、参与记录、计划单都按账号存），
 *  所以 switch 成功后调 refreshAll，并由 App 通过 key 变化重建当前页。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { api, type AccountRow } from '../api'
import { refreshAll, state, toast } from '../store'
import QrLogin from './QrLogin.vue'
import { canChangeAccount } from '../workGuard'

const open = ref(false)
const adding = ref(false)
const busy = ref(0)
const root = ref<HTMLElement | null>(null)

const rows = computed<AccountRow[]>(() => state.overview?.accounts ?? [])
const current = computed(() => rows.value.find((row) => row.current) ?? null)
const avatarUrl = computed(() => current.value?.avatar ?? '')
const initial = computed(() => (current.value?.nickname || '?').slice(0, 1))
const broken = ref(false)

/** 副标题。uid 太短时脱敏结果就是 `***`，那对辨认没有任何帮助，干脆不显示。 */
function sub(row: AccountRow) {
  if (!row.ready) return '凭据已失效，需重新扫码'
  const uid = row.uid && row.uid !== '***' ? ` · ${row.uid}` : ''
  return `Lv.${row.level}${uid}`
}

function toggle() {
  open.value = !open.value
  if (open.value) broken.value = false
}

function close(event: MouseEvent) {
  if (!root.value?.contains(event.target as Node)) {
    open.value = false
    adding.value = false
  }
}

async function pick(row: AccountRow) {
  if (row.current) {
    open.value = false
    return
  }
  if (!row.ready) {
    toast('这个账号的凭据已经没了，需要重新扫码（点下面的"添加账号"再扫一次）', 'warn')
    return
  }
  if(busy.value || !await canChangeAccount())return
  busy.value = row.id
  try {
    await api.switchAccount(row.id)
    await refreshAll()
    toast(`已切到 ${row.nickname}`, 'ok')
    open.value = false
  } catch (error) {
    toast(error instanceof Error ? error.message : '切换失败', 'danger')
  } finally {
    busy.value = 0
  }
}

async function forget(row: AccountRow) {
  if(row.current && !await canChangeAccount())return
  // 数据不删，只摘凭据 —— 但人不会读这句注释，所以要在确认框里说清楚。
  const ok = window.confirm(
    `不再使用「${row.nickname}」？\n\n凭据会被删掉，但它名下的参与记录、清理清单都会留着。\n想重新用，扫一次码就回来了。`,
  )
  if (!ok) return
  busy.value = row.id
  try {
    await api.removeAccount(row.id)
    await refreshAll()
    toast(`已移除 ${row.nickname}`, 'ok')
  } catch (error) {
    toast(error instanceof Error ? error.message : '移除失败', 'danger')
  } finally {
    busy.value = 0
  }
}

async function signedBy() {
  adding.value = false
  open.value = false
  await refreshAll()
}
async function addAccount(){if(await canChangeAccount())adding.value=true}

onMounted(() => document.addEventListener('click', close))
onUnmounted(() => document.removeEventListener('click', close))
</script>

<template>
  <div ref="root" class="relative">
    <button class="account-btn" :title="`当前账号：${current?.nickname ?? ''}`" @click.stop="toggle">
      <div class="text-right leading-tight hidden sm:block">
        <div class="text-[13.5px] font-medium">{{ current?.nickname }}</div>
        <div class="text-[11.5px] muted">Lv.{{ current?.level }}</div>
      </div>
      <img
        v-if="avatarUrl && !broken"
        :src="avatarUrl"
        alt=""
        referrerpolicy="no-referrer"
        class="w-8 h-8 rounded-full object-cover flex-none"
        style="border: 1px solid var(--line)"
        @error="broken = true"
      />
      <div
        v-else
        class="w-8 h-8 rounded-full grid place-items-center text-[13px] font-semibold flex-none"
        style="background: var(--accent-soft); color: var(--accent)"
      >
        {{ initial }}
      </div>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" class="flex-none muted">
        <path d="m6 9 6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
      </svg>
    </button>

    <div v-if="open" class="menu enter">
      <div class="px-3 pt-2.5 pb-1.5 text-[11.5px] muted flex items-center justify-between">
        <span>账号</span>
        <span v-if="rows.length > 1">{{ rows.length }} 个</span>
      </div>

      <div class="max-h-[260px] overflow-y-auto">
        <div
          v-for="row in rows"
          :key="row.id"
          class="account-row"
          :class="{ 'account-on': row.current, 'account-bad': !row.ready }"
          @click="pick(row)"
        >
          <div class="avatar">
            <img
              v-if="row.avatar && row.ready"
              :src="row.avatar"
              alt=""
              referrerpolicy="no-referrer"
              @error="($event.target as HTMLImageElement).style.display = 'none'"
            />
            <span>{{ row.nickname.slice(0, 1) || '?' }}</span>
          </div>
          <div class="min-w-0 flex-1">
            <div class="text-[13.5px] font-medium truncate">{{ row.nickname }}</div>
            <div class="text-[11.5px] muted truncate">{{ sub(row) }}</div>
          </div>
          <span v-if="row.current" class="text-[11px] px-1.5 py-0.5 rounded" style="background: var(--accent-soft); color: var(--accent)">当前</span>
          <button
            v-else
            class="del"
            :disabled="busy === row.id"
            title="移除这个账号"
            @click.stop="forget(row)"
          >
            ×
          </button>
        </div>
      </div>

      <div v-if="!adding" class="border-t p-1.5" style="border-color: var(--line-soft)">
        <button class="menu-item" @click.stop="addAccount">＋ 添加账号（扫码）</button>
      </div>

      <div v-else class="border-t p-3" style="border-color: var(--line-soft)">
        <div class="flex justify-center">
          <QrLogin :size="168" @done="signedBy" />
        </div>
        <p class="mt-2.5 text-[11.5px] muted text-center leading-relaxed">
          用另一个 B 站账号扫码<br />原来的账号不会被退出
        </p>
        <button class="menu-item mt-1" @click.stop="adding = false">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.account-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 6px 3px 4px;
  border-radius:var(--radius-panel);
  border: 1px solid transparent;
  background: transparent;
  color: var(--text);
  cursor: pointer;
  transition: all 0.14s ease;
}
.account-btn:hover {
  background: var(--surface-3);
  border-color: var(--line);
}
.menu {
  position: absolute;
  right: 0;
  top: calc(100% + 8px);
  width: 264px;
  z-index: 50;
  border-radius:var(--radius-panel);
  border: 1px solid var(--line);
  background: var(--surface-2);
  box-shadow: 0 14px 40px rgba(0, 0, 0, 0.36);
  overflow: hidden;
}
.account-row {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 7px 10px;
  cursor: pointer;
  transition: background 0.12s ease;
}
.account-row:hover {
  background: var(--surface-3);
}
.account-on {
  background: var(--accent-soft);
}
.account-bad {
  opacity: 0.6;
}
.avatar {
  position: relative;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  overflow: hidden;
  flex: none;
  display: grid;
  place-items: center;
  font-size:var(--text-meta);
  font-weight: 600;
  background: var(--surface-3);
  color: var(--accent);
}
.avatar img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.del {
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--muted);
  font-size:var(--text-body);
  line-height: 1;
  cursor: pointer;
}
.del:hover {
  background: color-mix(in srgb, var(--danger) 18%, transparent);
  color: var(--danger);
}
.menu-item {
  width: 100%;
  height: 30px;
  border: none;
  border-radius:var(--radius-control);
  background: transparent;
  color: var(--text);
  font-size:var(--text-meta);
  text-align: left;
  padding: 0 10px;
  cursor: pointer;
}
.menu-item:hover {
  background: var(--surface-3);
  color: var(--accent);
}
</style>
