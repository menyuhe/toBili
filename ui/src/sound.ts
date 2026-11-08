/** 提示音。
 *
 *  合成而不是加载一个 mp3：一个 wav 文件要让打包体积多几十 KB，还要在
 *  `--add-data` 里多写一行；而"两短一长"这种声音用振荡器几行就够了，
 *  音色和时长还能调。
 *
 *  浏览器有一条硬规矩：**用户没交互过，不许出声**。所以 AudioContext 一开始是
 *  suspended 的，必须靠第一次点击/按键把它解开。`unlock()` 就是干这个的，
 *  解不开时 `play()` 静默失败 —— 不能因为放不出声音就让界面报错。
 */

let ctx: AudioContext | null = null
let unlocked = false

function context(): AudioContext | null {
  if (ctx) return ctx
  const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctor) return null
  try {
    ctx = new Ctor()
  } catch {
    return null
  }
  return ctx
}

/** 在第一次真实交互时调用。重复调用无害。 */
export function unlock() {
  const audio = context()
  if (!audio) return
  if (audio.state === 'suspended') {
    void audio.resume().then(() => {
      unlocked = true
    })
  } else {
    unlocked = audio.state === 'running'
  }
}

/** 一声短音。`at` 是相对当前时间的秒数偏移，用来串成"叮咚"这类两音。 */
function tone(freq: number, at: number, duration: number, gain: number) {
  const audio = context()
  if (!audio) return
  const start = audio.currentTime + at
  const osc = audio.createOscillator()
  const amp = audio.createGain()
  osc.type = 'sine'
  osc.frequency.value = freq
  // 头尾都做一点斜坡，否则会听到"咔"的爆音 —— 这是方波开关断的经典毛病。
  amp.gain.setValueAtTime(0.0001, start)
  amp.gain.exponentialRampToValueAtTime(gain, start + 0.02)
  amp.gain.exponentialRampToValueAtTime(0.0001, start + duration)
  osc.connect(amp)
  amp.connect(audio.destination)
  osc.start(start)
  osc.stop(start + duration + 0.02)
}

export type SoundKind = 'prize' | 'done' | 'warn'

/** 播放提示音。放不出来就安静地什么都不做。 */
export function play(kind: SoundKind = 'prize') {
  if (!unlocked) unlock()
  const audio = context()
  if (!audio || audio.state !== 'running') return
  if (kind === 'prize') {
    // 上行两音：像门铃，比单音更容易从环境噪声里认出来。
    tone(880, 0, 0.12, 0.16)
    tone(1320, 0.14, 0.2, 0.14)
  } else if (kind === 'done') {
    tone(660, 0, 0.1, 0.1)
  } else {
    tone(400, 0, 0.16, 0.12)
    tone(300, 0.18, 0.22, 0.12)
  }
}

/** 桌面通知。没授权、或者浏览器不支持，都安静跳过。 */
export function desktop(title: string, body: string) {
  if (!('Notification' in window)) return
  if (Notification.permission === 'granted') {
    new Notification(title, { body, icon: '/icon.png' })
    return
  }
  if (Notification.permission === 'default') {
    void Notification.requestPermission()
  }
}
