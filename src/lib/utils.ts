import type { SourceItem } from '../types'

export function connectivityRate(item: SourceItem): number {
  const { ok, total } = item.probe
  if (!total) return 0
  return ok / total
}

export type Tone = 'ok' | 'warn' | 'bad'

export function connectivityTone(rate: number): Tone {
  if (rate >= 0.8) return 'ok'
  if (rate >= 0.5) return 'warn'
  return 'bad'
}

export interface AgeInfo {
  text: string
  stale: boolean
}

export function formatAge(ageHours: number): AgeInfo {
  if (ageHours < 0.5) return { text: '源刚刚更新', stale: false }
  if (ageHours < 1) {
    return { text: `源 ${Math.round(ageHours * 60)} 分钟前更新`, stale: false }
  }
  if (ageHours < 30) {
    const h = ageHours < 10 ? ageHours.toFixed(1) : ageHours.toFixed(0)
    return { text: `源 ${h} 小时前更新`, stale: false }
  }
  return { text: `源 ${Math.floor(ageHours / 24)} 天没更新，可能已停更`, stale: true }
}

export function formatCount(n: number): string {
  return n.toLocaleString('zh-CN')
}

export function sourceStatusText(item: SourceItem): { text: string; tone: Tone } {
  const s = item.src_http
  if (!s || s.code === 0) return { text: '源站本次无响应', tone: 'bad' }
  if (s.code >= 400) return { text: `源站 HTTP ${s.code}`, tone: 'warn' }
  return { text: `源站 ${s.code} · ${s.ms}ms`, tone: 'ok' }
}
