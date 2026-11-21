import { Copy, QrCode } from 'lucide-react'
import type { SourceItem } from '../types'
import type { Tone } from '../lib/utils'
import { connectivityRate, connectivityTone, formatAge, formatCount, sourceStatusText } from '../lib/utils'

interface SourceCardProps {
  item: SourceItem
  highlighted: boolean
  onCopy: (item: SourceItem) => void
  onQr: (item: SourceItem) => void
}

const rateToneText: Record<Tone, string> = {
  ok: 'text-ink',
  warn: 'text-warn',
  bad: 'text-bad',
}

const srcToneText: Record<Tone, string> = {
  ok: 'text-ink-soft',
  warn: 'text-warn',
  bad: 'text-bad',
}

export default function SourceCard({ item, highlighted, onCopy, onQr }: SourceCardProps) {
  const rate = connectivityRate(item)
  const tone = connectivityTone(rate)
  const age = formatAge(item.age_h)
  const src = sourceStatusText(item)

  return (
    <article className="flex flex-col gap-4 rounded-lg bg-raised p-5 shadow-1 transition-shadow duration-200 hover:shadow-2">
      {/* 标题 + 更新频率 */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 font-display text-[15px] font-semibold leading-5">
            {highlighted && (
              <span
                className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-accent"
                aria-label="推荐"
                title="本组质量最高"
              />
            )}
            <span className="truncate">{item.tag}</span>
          </h3>
          {item.note && (
            <p className="mt-1 line-clamp-2 text-[12px] leading-4 text-ink-soft">{item.note}</p>
          )}
        </div>
        <span className="shrink-0 rounded-full bg-surface px-2.5 py-1 text-[11px] font-medium text-ink-soft">
          {item.freq}
        </span>
      </div>

      {/* 节点数 */}
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[26px] font-medium leading-none tabular-nums">
          {formatCount(item.count)}
        </span>
        <span className="text-[12px] text-ink-soft">节点</span>
      </div>

      {/* 可连率 */}
      <div>
        <div className="flex items-center justify-between text-[12px]">
          <span className="text-ink-soft">可连率</span>
          <span className={`font-mono tabular-nums ${rateToneText[tone]}`}>
            {item.probe.ok}/{item.probe.total} · {Math.round(rate * 100)}%
          </span>
        </div>
        <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-line">
          <div
            className="h-full rounded-full bg-ink transition-[width] duration-300"
            style={{ width: `${Math.round(rate * 100)}%` }}
          />
        </div>
      </div>

      {/* 新鲜度 + 源站状态 */}
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[12px]">
        <span className={age.stale ? 'font-medium text-bad' : 'text-ink-soft'}>{age.text}</span>
        <span className="text-line" aria-hidden="true">
          ·
        </span>
        <span className={srcToneText[src.tone]}>{src.text}</span>
      </div>

      {/* 操作 */}
      <div className="mt-auto flex gap-2 border-t border-line pt-4">
        <button
          type="button"
          onClick={() => onCopy(item)}
          className="inline-flex flex-1 items-center justify-center gap-2 rounded-full bg-ink px-4 py-2.5 text-[13px] font-medium text-raised shadow-1 transition-opacity duration-200 hover:opacity-85"
        >
          <Copy size={15} strokeWidth={1.5} />
          复制链接
        </button>
        <button
          type="button"
          onClick={() => onQr(item)}
          aria-label={`查看 ${item.tag} 二维码`}
          className="inline-flex items-center justify-center gap-2 rounded-full border border-line bg-raised px-4 py-2.5 text-[13px] font-medium text-ink transition-colors duration-200 hover:bg-surface"
        >
          <QrCode size={15} strokeWidth={1.5} />
          二维码
        </button>
      </div>
    </article>
  )
}
