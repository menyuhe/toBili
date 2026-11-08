/** 极简 Markdown 渲染。
 *
 *  只支持整理稿真正用到的那几种标记：标题、段落、引用、分隔行、粗体、链接。
 *  引一个完整的 Markdown 库会带来一堆我们不需要的语法（表格、代码块、脚注），
 *  以及一个"预览和专栏里长得不一样"的风险 —— 渲染得越花，对不上号的地方越多。
 *
 *  先转义再替换。顺序反了，标题里出现一个 `<` 就会把后面的结构吃掉。
 */

const RULE = /^[-─═✦\s]{3,}$/

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function inline(escaped: string): string {
  return escaped
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(
      /(https?:\/\/[^\s<]+)/g,
      '<a href="$1" target="_blank" rel="noreferrer">$1</a>',
    )
}

export function renderMarkdown(text: string): string {
  const out: string[] = []
  let paragraph: string[] = []

  const flush = () => {
    if (!paragraph.length) return
    out.push(`<p>${inline(paragraph.join('<br />'))}</p>`)
    paragraph = []
  }

  for (const raw of text.split('\n')) {
    const line = raw.trimEnd()
    if (!line.trim()) {
      flush()
      continue
    }
    if (line.startsWith('### ')) {
      flush()
      out.push(`<h3>${inline(escapeHtml(line.slice(4)))}</h3>`)
    } else if (line.startsWith('## ')) {
      flush()
      out.push(`<h2>${inline(escapeHtml(line.slice(3)))}</h2>`)
    } else if (line.startsWith('# ')) {
      flush()
      out.push(`<h1>${inline(escapeHtml(line.slice(2)))}</h1>`)
    } else if (line.startsWith('> ')) {
      flush()
      out.push(`<blockquote>${inline(escapeHtml(line.slice(2)))}</blockquote>`)
    } else if (RULE.test(line)) {
      flush()
      out.push(`<div class="sep">${escapeHtml(line)}</div>`)
    } else {
      paragraph.push(escapeHtml(line))
    }
  }
  flush()
  return out.join('')
}
