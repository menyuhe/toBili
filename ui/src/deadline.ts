export function deadlineTone(value: string | null | undefined, now = Date.now()): string {
  if (!value) return ''
  const remaining = Date.parse(value) - now
  if (!Number.isFinite(remaining) || remaining <= 0) return ''
  if (remaining < 24 * 3600_000) return 'deadline-urgent'
  if (remaining < 48 * 3600_000) return 'deadline-soon'
  return ''
}

export function deadlineHint(tone: string): string {
  return tone === 'deadline-urgent' ? '不足 24 小时截止' : tone === 'deadline-soon' ? '不足 48 小时截止' : ''
}
