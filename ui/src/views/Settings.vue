<script setup lang="ts">
/** 设置。
 *
 *  四块东西放一页：参与文案、模型接入、通知（钉钉）、提示音。默认只展开
 *  「参与文案」——其余三块是配一次就不再动的，收起来之后一屏就能看全。
 *
 *  密钥类字段（钉钉加签、模型 api_key）**永远不显示已存的值**，只显示"设过了"。
 *  输入框留空 = 不改。这样即使有人站在背后看屏幕，也看不到密钥；而"保存一次
 *  没改密钥的配置就把密钥清了"这种坑也就不会发生。
 */
import { onMounted, ref } from 'vue'

import { api, req, type NotifyState, type Settings } from '../api'
import SettingCard from '../components/SettingCard.vue'
import BlockList from '../components/BlockList.vue'
import ParticipationPace from '../components/ParticipationPace.vue'
import { setSound, state, toast } from '../store'
import { play } from '../sound'

// 哪一块是展开的。默认只开第一块：这一页有二十多个输入框，全铺开等于让人自己找。
const openText = ref(false)
const openLlm = ref(false)
const openNotify = ref(false)
const openSound = ref(false)
const openPace = ref(false)
const openBlocks = ref(false)
const blockCount = ref(0)

/** 三条路按可靠性排序：模板最稳，评论区次之，模型最飘。任何一条失败都往下退，
 *  所以这页的重点不是"选哪个"，而是"兜底那句一定要能发出去"。 */
const MODES = [
  { value: 'custom', label: '自己写模板', hint: '使用固定模板' },
  { value: 'random_comment', label: '抓评论区', hint: '从原动态评论里挑一条真人写的' },
  { value: 'llm', label: '让模型写', hint: '读正文现写一句，要先配好下面的模型' },
]

const SOURCE_LABEL: Record<string, string> = {
  custom: '模板',
  random_comment: '评论区',
  llm: '模型',
  fallback: '兜底',
}

const settings = ref<Settings | null>(null)
const notify = ref<NotifyState | null>(null)
const loading = ref(true)
const busy = ref('')

const hook = ref('')
const secret = ref('')
const nEnabled = ref(false)

const baseUrl = ref('')
const model = ref('')
const apiKey = ref('')
const llmOn = ref(false)
const scanLlm = ref(false)

const mode = ref('custom')
const custom = ref('')
const fallback = ref('')
const draft = ref<{ text: string; source: string; note: string; fallback: boolean } | null>(null)
const ta = ref<HTMLTextAreaElement | null>(null)

function applyText(saved: Settings) {
  mode.value = saved.mode
  custom.value = saved.custom
  fallback.value = saved.fallback
}

async function load() {
  loading.value = true
  try {
    const [saved, n] = await Promise.all([api.settings(), api.notifyState()])
    blockCount.value = (await req<{ items: unknown[] }>('/api/blocks').catch(() => ({ items: [] }))).items.length
    settings.value = saved
    applyText(saved)
    notify.value = n
    nEnabled.value = n.enabled
    baseUrl.value = saved.llm.base_url
    model.value = saved.llm.model
    scanLlm.value = saved.llm.scan_enabled
    llmOn.value = saved.llm.enabled
  } catch (error) {
    toast(error instanceof Error ? error.message : '读取设置失败', 'danger')
  } finally {
    loading.value = false
  }
}

function insertVar(name: string) {
  // 插在光标处，不是追加在末尾：模板常常是"来啦，{up} 这次试试运气"这种中间插入。
  const el = ta.value
  if (!el) {
    custom.value = (custom.value + name).slice(0, 60)
    return
  }
  const start = el.selectionStart ?? custom.value.length
  const end = el.selectionEnd ?? start
  custom.value = (custom.value.slice(0, start) + name + custom.value.slice(end)).slice(0, 60)
  requestAnimationFrame(() => {
    el.focus()
    const at = Math.min(start + name.length, custom.value.length)
    el.setSelectionRange(at, at)
  })
}

async function saveText() {
  busy.value = 'text'
  try {
    settings.value = await api.saveText({
      mode: mode.value,
      custom: custom.value,
      fallback: fallback.value,
    })
    applyText(settings.value)
    toast('文案设置已保存', 'ok')
  } catch (error) {
    toast(error instanceof Error ? error.message : '保存失败', 'danger')
  } finally {
    busy.value = ''
  }
}

async function previewText() {
  busy.value = 'preview'
  draft.value = null
  try {
    draft.value = await api.previewText({
      up_name: '永夜游戏',
      title: '【评论区抽喜加一，11月9日开奖】赛车+校园恋爱！',
      body: '关注+转发+评论，抽 3 位送游戏激活码。',
    })
  } catch (error) {
    toast(error instanceof Error ? error.message : '预览失败', 'danger')
  } finally {
    busy.value = ''
  }
}

async function saveNotify() {
  busy.value = 'notify'
  try {
    // 输入框留空 = 不改。后端拿到 undefined 才不会把已存的地址覆盖成空串。
    notify.value = await api.saveNotify({
      webhook: hook.value.trim() || undefined,
      secret: secret.value,
      enabled: nEnabled.value,
    })
    secret.value = ''
    hook.value = ''
    toast(nEnabled.value ? '通知已开启' : '已保存（通知关闭）', 'ok')
  } catch (error) {
    toast(error instanceof Error ? error.message : '保存失败', 'danger')
  } finally {
    busy.value = ''
  }
}

async function testNotify() {
  busy.value = 'test'
  try {
    await api.testNotify()
    toast('测试消息已发出，去群里看看', 'ok')
  } catch (error) {
    toast(error instanceof Error ? error.message : '发不出去', 'danger')
  } finally {
    busy.value = ''
  }
}

async function clearNotify() {
  if (!window.confirm('清掉钉钉配置？密钥会一起删掉，之后要重填。')) return
  try {
    notify.value = await api.clearNotify()
    nEnabled.value = false
    toast('已清除', 'ok')
  } catch (error) {
    toast(error instanceof Error ? error.message : '清除失败', 'danger')
  }
}

async function saveLlm() {
  busy.value = 'llm'
  try {
    settings.value = await api.saveLlm({
      base_url: baseUrl.value,
      model: model.value,
      api_key: apiKey.value,
      enabled: llmOn.value,
      scan_enabled: scanLlm.value,
    })
    apiKey.value = ''
    toast('已保存', 'ok')
  } catch (error) {
    toast(error instanceof Error ? error.message : '保存失败', 'danger')
  } finally {
    busy.value = ''
  }
}

async function testLlm() {
  busy.value = 'llmt'
  try {
    const result = await api.testLlm()
    toast(`通了，它回了：${result.reply}`, 'ok')
  } catch (error) {
    toast(error instanceof Error ? error.message : '连不上', 'danger')
  } finally {
    settings.value = await api.settings().catch(() => settings.value)
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="settings-grid">
    <div v-if="loading" class="panel p-8 text-center text-[13.5px] muted">读取中…</div>

    <template v-else>
      <SettingCard v-model:shown="openPace" title="参与节奏"><ParticipationPace/></SettingCard>
      <!-- 参与文案 -->
      <SettingCard
        v-model:shown="openText"
        title="参与文案"
      >
        <template #right>
          <span class="chip">{{ MODES.find((m) => m.value === mode)?.label }}</span>
        </template>

        <div class="p-4 space-y-3">
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <button
              v-for="m in MODES"
              :key="m.value"
              class="mode"
              :class="{ 'mode-on': mode === m.value }"
              @click="mode = m.value"
            >
              <div class="text-[13px] font-medium">{{ m.label }}</div>
            </button>
          </div>

          <div v-if="mode === 'custom'">
            <label class="lbl">
              模板
              <span class="muted">60 字以内</span>
            </label>
            <textarea
              ref="ta"
              v-model="custom"
              class="ta"
              rows="3"
              maxlength="60"
              placeholder="来啦，{up} 这次试试运气"
            />
            <div class="flex items-center gap-1.5 mt-1.5">
              <button v-for="v in ['{up}', '{title}', '{prize}']" :key="v" class="var" @click="insertVar(v)">
                {{ v }}
              </button>
              <span class="text-[11.5px] muted ml-auto">{{ custom.length }}/60</span>
            </div>
          </div>

          <div>
            <label class="lbl">备用文案</label>
            <input v-model="fallback" class="ipt" maxlength="60" placeholder="转发一下" />
          </div>

          <div class="flex flex-wrap gap-2 pt-1">
            <button class="btn" :disabled="busy === 'text'" @click="saveText">
              {{ busy === 'text' ? '保存中…' : '保存' }}
            </button>
            <button class="btn" :disabled="busy === 'preview'" @click="previewText">
              {{ busy === 'preview' ? '生成中…' : '预览一条' }}
            </button>
          </div>

          <div v-if="draft" class="draft">
            <div class="flex items-center gap-2 mb-1.5">
              <span class="chip" :class="draft.fallback ? 'chip-warn' : 'chip-ok'">
                {{ SOURCE_LABEL[draft.source] ?? draft.source }}
              </span>
              <span v-if="draft.note" class="text-[11.5px] muted">{{ draft.note }}</span>
            </div>
            <div class="text-[13.5px] leading-relaxed">{{ draft.text }}</div>
          </div>
        </div>
      </SettingCard>

      <!-- 模型接入 -->
      <SettingCard
        v-model:shown="openLlm"
        title="模型接入"
      >
        <template #right>
          <span class="chip" :class="settings?.llm.health?.ok===false ? 'chip-warn' : settings?.llm.health?.ok ? 'chip-ok' : ''">
            {{ !settings?.llm.ready ? '未配置' : settings.llm.health?.ok===false ? '调用异常' : settings.llm.health?.ok ? '已连通' : '待测试' }}
          </span>
        </template>

        <div class="p-4 space-y-3">
          <div>
            <label class="lbl">接口地址</label>
            <input v-model="baseUrl" class="ipt" placeholder="https://api.example.com/v1" />
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label class="lbl">模型名</label>
              <input v-model="model" class="ipt" placeholder="deepseek-chat / gpt-4o-mini …" />
            </div>
            <div>
              <label class="lbl">
                API Key
                <span v-if="settings?.llm.key_set" class="chip chip-ok ml-1">已设置</span>
              </label>
              <input v-model="apiKey" class="ipt" type="password" placeholder="留空表示不改" />
            </div>
          </div>
          <label class="flex items-center gap-2 text-[13px] cursor-pointer select-none">
            <input v-model="llmOn" type="checkbox" />
            用模型生成文案
          </label>
          <label class="flex items-center gap-2 text-[13px]"><input v-model="scanLlm" type="checkbox" /> 用模型识别活动</label>
          <p v-if="settings?.llm.health?.error" class="warning">{{settings.llm.health.error}}</p>
          <div class="flex gap-2 pt-1">
            <button class="btn" :disabled="busy === 'llm'" @click="saveLlm">
              {{ busy === 'llm' ? '保存中…' : '保存' }}
            </button>
            <button class="btn" :disabled="busy === 'llmt' || !settings?.llm.ready" @click="testLlm">
              {{ busy === 'llmt' ? '连接中…' : '测试连接' }}
            </button>
          </div>
        </div>
      </SettingCard>

      <!-- 通知 -->
      <SettingCard
        v-model:shown="openNotify"
        title="钉钉通知"
      >
        <template #right>
          <span class="chip" :class="notify?.enabled && notify?.has_webhook ? 'chip-ok' : ''">
            {{ notify?.enabled && notify?.has_webhook ? '已开启' : '未开启' }}
          </span>
        </template>

        <div class="p-4 space-y-3">
          <div>
            <label class="lbl">Webhook 地址</label>
            <input
              v-model="hook"
              class="ipt"
              :placeholder="notify?.has_webhook ? `已配置：${notify.webhook}（留空表示不改）` : 'https://oapi.dingtalk.com/robot/send?access_token=…'"
            />
          </div>
          <div>
            <label class="lbl">
              加签密钥
              <span v-if="notify?.has_secret" class="chip chip-ok ml-1">已设置</span>
            </label>
            <input
              v-model="secret"
              class="ipt"
              type="password"
              placeholder="留空不修改"
            />
          </div>

          <label class="flex items-center gap-2 text-[13px] cursor-pointer select-none">
            <input v-model="nEnabled" type="checkbox" />
            启用钉钉推送
          </label>

          <div class="flex flex-wrap gap-2 pt-1">
            <button class="btn" :disabled="busy === 'notify'" @click="saveNotify">
              {{ busy === 'notify' ? '保存中…' : '保存' }}
            </button>
            <button class="btn" :disabled="busy === 'test' || !notify?.has_webhook" @click="testNotify">
              {{ busy === 'test' ? '发送中…' : '发一条测试' }}
            </button>
            <button class="btn danger" @click="clearNotify">清除配置</button>
          </div>
        </div>
      </SettingCard>

      <!-- UP 黑名单：从「管理设置」那一组挪进来，和通知、提示音同级 -->
      <SettingCard v-model:shown="openBlocks" title="UP 黑名单">
        <template #right>
          <span class="chip" :class="blockCount ? 'chip-warn' : ''">{{ blockCount }} 个</span>
        </template>
        <BlockList @count="blockCount = $event" />
      </SettingCard>

      <!-- 提示音 -->
      <SettingCard v-model:shown="openSound" title="提示音">
        <template #right>
          <span class="chip" :class="state.soundOn ? 'chip-ok' : ''">
            {{ state.soundOn ? '开' : '关' }}
          </span>
        </template>
        <div class="p-4 space-y-3">
          <div class="flex items-center justify-between gap-3">
            <label class="flex items-center gap-2 text-[13px] cursor-pointer select-none">
              <input :checked="state.soundOn" type="checkbox" @change="setSound(!state.soundOn)" />
              启用提示音
            </label>
            <button class="btn" @click="play('prize')">试听</button>
          </div>
        </div>
      </SettingCard>

    </template>
  </div>
</template>

<style scoped>
.settings-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px;max-width:1100px;margin:auto}.settings-grid>.panel:only-child{grid-column:1/-1}@media(max-width:700px){.settings-grid{grid-template-columns:1fr}}
.lbl {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 5px;
}
/* .ipt 已挪到 styles.css 里：活动索引和定时页也在用，写在 scoped 里只有这一页生效。 */
.ta {
  width: 100%;
  padding: 8px 10px;
  border-radius:var(--radius-control);
  border: 1px solid var(--line);
  background: var(--surface-2);
  color: var(--text);
  font-size:var(--text-control);
  line-height: 1.65;
  outline: none;
  resize: vertical;
}
.ta:focus {
  border-color: var(--accent);
}
/* 三张并排的"路"，选中态只换边框和底色，不换字号 —— 换字号会让整行跳一下。 */
.mode {
  text-align: left;
  padding: 9px 11px;
  border-radius:var(--radius-panel);
  border: 1px solid var(--line);
  background: var(--surface-2);
  color: var(--text);
  cursor: pointer;
  transition: all 0.14s ease;
}
.mode:hover {
  border-color: var(--accent);
}
.mode-on {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.var {
  height: 22px;
  padding: 0 7px;
  border-radius:var(--radius-control);
  border: 1px solid var(--line);
  background: var(--surface-2);
  color: var(--muted);
  font-size: 12px;
  font-family: var(--mono, ui-monospace, monospace);
  cursor: pointer;
}
.var:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.draft {
  padding: 10px 12px;
  border-radius:var(--radius-panel);
  border: 1px dashed var(--line);
  background: var(--surface-2);
}
</style>
