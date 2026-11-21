import type { Group, SourceItem } from '../types'
import SourceCard from './SourceCard'

interface GroupSectionProps {
  group: Group
  index: number
  highlight: Set<string>
  onCopy: (item: SourceItem) => void
  onQr: (item: SourceItem) => void
}

export default function GroupSection({
  group,
  index,
  highlight,
  onCopy,
  onQr,
}: GroupSectionProps) {
  return (
    <section className="mb-11">
      <div className="mb-4">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="font-display text-[17px] font-semibold leading-6">{group.title}</h2>
          <span className="text-[12px] text-ink-soft">{group.kern}</span>
        </div>
        <p className="mt-0.5 text-[12px] text-ink-soft">{group.clients}</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {group.items.map((item, i) => (
          <div
            key={item.url}
            className="animate-rise"
            style={{ animationDelay: `${index * 60 + i * 40}ms` }}
          >
            <SourceCard
              item={item}
              highlighted={highlight.has(item.url)}
              onCopy={onCopy}
              onQr={onQr}
            />
          </div>
        ))}
      </div>
    </section>
  )
}
