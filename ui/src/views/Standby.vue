<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
import {useRouter} from 'vue-router'
import {api,req,type AccountRow} from '../api'
import {canChangeAccount} from '../workGuard'
import {refreshAll,toast} from '../store'
import Countdown from '../components/Countdown.vue'
import Icon from '../components/Icon.vue'
import AccountAvatar from '../components/AccountAvatar.vue'
type Item=AccountRow&{active:{label?:string;note?:string;since?:string};next?:{label:string;at:number};running:boolean;enabled:string[];pending:number;hourly_used:number;recent?:{at:string;note:string};uid_full?:string;unread?:number|null}
const rows=ref<Item[]>([]),error=ref(''),at=ref(''),busy=ref(0),loading=ref(true)
const router=useRouter();let alive=true,timer:ReturnType<typeof setInterval>|undefined
const running=computed(()=>rows.value.filter(r=>r.running).length)
/** 侧栏那个红点只算**当前账号**的未读 —— 但页面右侧摆着好几个号，人自然以为
 *  它是合计。所以每张卡的消息按钮上要写清"这个号有几条"。
 *  null 表示这一号还没读过消息（不是"没有新消息"），那种情况不显示数字。 */
const unreadTotal=computed(()=>rows.value.reduce((n,r)=>n+(r.unread||0),0))
function when(v:string|number){return new Date(typeof v==='number'?v*1000:v).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})}
/** 一张卡片的性格由这四种状态决定，颜色也由它决定：
 *  正在跑 / 已开启待机 / 未开启 / 需登录。以前只有一条细边框，
 *  一屏六张卡看不出哪张在动。 */
function tone(r:Item){if(!r.ready)return 'tone-warn';if(r.active.label)return 'tone-accent';if(r.running)return 'tone-ok';return 'tone-discover'}
function stateText(r:Item){if(!r.ready)return '需登录';if(r.active.label)return '执行中';if(r.running)return '待机';return '未开启'}
async function load(){try{const data=await req<{items:Item[];at:string}>('/api/standby');if(alive){rows.value=data.items;at.value=data.at;error.value=''}}catch(e){if(alive)error.value=String(e)}finally{loading.value=false}}
async function open(r:Item,path:string){if(busy.value||!r.ready||!await canChangeAccount())return;busy.value=r.id;try{if(!r.current){await api.switchAccount(r.id);await refreshAll()}await router.push(path)}catch(e){toast(String(e),'danger')}finally{busy.value=0}}
onMounted(()=>{void load();timer=setInterval(()=>void load(),5000)})
onUnmounted(()=>{alive=false;if(timer)clearInterval(timer)})
</script>
<template><div class="standby-workspace">
<section class="panel page-heading">
  <div><h2>挂机运作</h2><span class="heading-sub">{{rows.length}} 个账号 · {{running}} 个已开启自动任务<span v-if="unreadTotal"> · 消息 {{unreadTotal}} 条未看</span><span v-if="at"> · 更新于 {{when(at)}}</span></span></div>
  <div class="toolbar"><button @click="load"><Icon name="refresh" :size="16"/>刷新</button></div>
</section>
<p v-if="error" class="warning" role="alert">{{error}}</p>
<p v-if="loading&&!rows.length" class="empty-state">读取中…</p>

<div class="account-grid">
<article v-for="r in rows" :key="r.id" class="panel account-card" :class="tone(r)">
  <header class="account-heading">
    <AccountAvatar :src="r.avatar" :name="r.nickname" :size="38"/>
    <div class="account-name"><strong>{{r.nickname}}</strong><span class="uid-line">UID {{r.uid_full||r.uid}}<template v-if="r.current"> · 当前账号</template></span></div>
    <span class="chip state-chip">{{stateText(r)}}</span>
  </header>

  <div class="account-body">
    <div class="label-row">
      <span class="label">待参与</span>
      <span v-if="r.next" class="next-line"><Icon name="clock" :size="14"/><Countdown :at="r.next.at"/></span>
      <span v-else class="next-line muted">暂无排期</span>
    </div>
    <div class="hero"><strong>{{r.pending}}</strong><span class="hero-unit">条</span></div>
    <span class="card-meta">{{r.active.label||(!r.ready?'登录后继续':r.running?'等待下一项':'暂无任务')}}{{r.active.since?' · 开始于 '+when(r.active.since):''}}</span>

    <div class="card-divider" />

    <p class="stat-line">近 1 小时尝试 <b>{{r.hourly_used}}</b> · 已启用任务 <b>{{r.enabled.length}}</b> · 下一项 <b>{{r.next?when(r.next.at):'—'}}</b></p>

    <div class="tag-row">
      <span v-for="label in r.enabled" :key="label" class="tag">{{label}}</span>
      <span v-if="!r.enabled.length" class="tag tag-muted">{{r.running?'订阅自动更新':'未开启定时任务'}}</span>
    </div>

    <p v-if="r.active.note" class="card-note">{{r.active.note}}</p>
    <p class="card-note muted">最近：{{r.recent?when(r.recent.at)+' · '+r.recent.note:'暂无运行记录'}}</p>
  </div>

  <footer class="account-actions">
    <button :disabled="!!busy||!r.ready" @click="open(r,'/auto')"><Icon name="settings" :size="15"/>任务设置</button>
    <button :disabled="!!busy||!r.ready" @click="open(r,'/activity')">运行记录</button>
    <button :disabled="!!busy||!r.ready" :title="r.unread!=null?(r.unread?`这个号有 ${r.unread} 条未看消息`:'这个号没有未看消息'):(r.current?'还没读过消息，点进去读一次':'切到这个号才能读到它有几条未看')" @click="open(r,'/inbox')"><Icon name="inbox" :size="15"/>消息<span v-if="r.unread" class="msg-badge">{{r.unread}}</span></button>
  </footer>
</article>
</div>
</div></template>
<style scoped>
.standby-workspace{max-width:1440px;margin:auto;display:flex;flex-direction:column;gap:18px}
.account-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,430px),1fr));gap:18px;align-items:stretch}

/* 卡片语言：彩色描边 + 同色极淡底。四种状态四种色，
   一眼能分出哪张在跑、哪张只是开着、哪张得先去登录。 */
.account-card{--card-tone:var(--line);display:flex;flex-direction:column;overflow:hidden;border:1px solid color-mix(in srgb,var(--card-tone) 42%,var(--line));border-top:3px solid var(--card-tone);background:color-mix(in srgb,var(--card-tone) 6%,var(--surface))}
.tone-accent{--card-tone:var(--accent)}
.tone-ok{--card-tone:var(--ok)}
.tone-warn{--card-tone:var(--warn)}
.tone-discover{--card-tone:var(--discover)}

.account-heading{display:flex;align-items:center;gap:12px;padding:16px 18px 14px}
.account-name{min-width:0;flex:1;display:grid;gap:3px}
.account-name strong{font-size:var(--text-heading);font-weight:650;line-height:1.3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.account-name span{font-size:var(--text-small);color:var(--muted);font-variant-numeric:tabular-nums}
/* UID 得能整串读完：打码成 3546…8470 或者 *** 等于没写，人认不出这是自己的哪个号。 */
.uid-line{letter-spacing:.2px}
.state-chip{flex:none;background:color-mix(in srgb,var(--card-tone) 14%,var(--surface));color:var(--card-tone);border-color:transparent;font-weight:600}

.account-body{display:flex;flex-direction:column;gap:8px;padding:0 18px 16px}
.label-row{display:flex;align-items:center;justify-content:space-between;gap:10px;min-height:22px}
.label{font-size:var(--text-meta);color:var(--text-dim)}
.next-line{display:inline-flex;align-items:center;gap:5px;font-size:var(--text-meta);color:var(--card-tone);font-variant-numeric:tabular-nums}
.hero{display:flex;align-items:baseline;gap:6px}
.hero strong{font-size:34px;font-weight:700;line-height:1;letter-spacing:-1px;font-variant-numeric:tabular-nums;color:var(--text)}
.hero-unit{font-size:var(--text-body);color:var(--muted)}
.card-meta{font-size:var(--text-small);color:var(--muted);font-variant-numeric:tabular-nums}
.card-divider{height:1px;margin:6px 0 2px;background:color-mix(in srgb,var(--card-tone) 22%,var(--line-soft))}
.stat-line{font-size:var(--text-meta);color:var(--muted);line-height:1.6}
.stat-line b{color:var(--text-dim);font-weight:600;font-variant-numeric:tabular-nums}
.tag-row{display:flex;flex-wrap:wrap;gap:6px;min-height:24px}
.tag{display:inline-flex;align-items:center;height:24px;padding:0 9px;border-radius:var(--radius-tiny);font-size:var(--text-small);font-weight:500;color:color-mix(in srgb,var(--card-tone) 80%,var(--text));background:color-mix(in srgb,var(--card-tone) 12%,var(--surface));border:1px solid color-mix(in srgb,var(--card-tone) 24%,transparent)}
.tag-muted{color:var(--muted);background:var(--surface-2);border-color:var(--line)}
.card-note{font-size:var(--text-small);line-height:1.6;color:var(--text-dim);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.card-note.muted{color:var(--muted)}

.account-actions{display:flex;gap:6px;padding:12px 14px;margin-top:auto;border-top:1px solid color-mix(in srgb,var(--card-tone) 18%,var(--line-soft))}
.account-actions button{flex:1;min-width:0;height:var(--control-height-small);padding:0 6px;font-size:var(--text-small);background:color-mix(in srgb,var(--surface) 82%,transparent);border-color:color-mix(in srgb,var(--card-tone) 22%,var(--line))}
.account-actions button:hover:not(:disabled){border-color:var(--card-tone);color:var(--card-tone)}
/* 消息按钮上的数字：一眼看出这个号的未读，而不是去侧栏那个合计里猜。 */
.msg-badge{display:inline-flex;align-items:center;justify-content:center;min-width:17px;height:17px;margin-left:5px;padding:0 5px;border-radius:var(--radius-pill);background:var(--danger);color:#fff;font-size:var(--text-micro);font-weight:650;line-height:1;font-variant-numeric:tabular-nums}
</style>
