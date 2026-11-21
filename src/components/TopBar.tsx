import { Search, Copy, Sparkle } from 'lucide-react'

interface TopBarProps {
  query: string
  onQueryChange: (v: string) => void
  onlyAlive: boolean
  onToggleAlive: () => void
  onlyCn: boolean
  onToggleCn: () => void
  visibleCount: number
  totalCount: number
  updated: string
  onCopyAll: () => void
}

interface ToggleProps {
  label: string
  pressed: boolean
  onClick: () => void
}

function Toggle({ label, pressed, onClick }: ToggleProps) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={`shrink-0 rounded-full px-3.5 py-1.5 text-[13px] font-medium transition-colors duration-200 ${
        pressed
          ? 'bg-ink text-raised shadow-1'
          : 'border border-line bg-raised text-ink-soft hover:text-ink'
      }`}
    >
      {label}
    </button>
  )
}

export default function TopBar({
  query,
  onQueryChange,
  onlyAlive,
  onToggleAlive,
  onlyCn,
  onToggleCn,
  visibleCount,
  totalCount,
  updated,
  onCopyAll,
}: TopBarProps) {
  return (
    <header className="mb-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2.5 font-display text-[28px] font-bold leading-8 tracking-tight">
            <Sparkle size={24} strokeWidth={1.75} className="text-ink" aria-hidden="true" />
            节点订阅
          </h1>
          <p className="mt-2 text-[13px] text-ink-soft">
            免费节点订阅索引 · 每 3 小时自动采集 · 更新于 {updated}
          </p>
        </div>
        <button
          type="button"
          onClick={onCopyAll}
          className="inline-flex items-center gap-2 rounded-full border border-line bg-raised px-4 py-2 text-[13px] font-medium text-ink shadow-1 transition-colors duration-200 hover:bg-surface"
        >
          <Copy size={15} strokeWidth={1.5} />
          复制全部链接
        </button>
      </div>

      <div className="mt-6 flex flex-col gap-3 lg:flex-row lg:items-center">
        <label className="relative flex-1">
          <span className="sr-only">搜索订阅源</span>
          <Search
            size={17}
            strokeWidth={1.5}
            className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ink-soft"
          />
          <input
            type="search"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder="搜索订阅源、说明或链接…"
            className="h-11 w-full rounded-full border border-line bg-raised pl-11 pr-4 text-[14px] text-ink shadow-1 outline-none transition-colors duration-200 placeholder:text-ink-soft focus:border-ink"
          />
        </label>

        <div className="flex items-center gap-2">
          <Toggle label="只看可连 ≥80%" pressed={onlyAlive} onClick={onToggleAlive} />
          <Toggle label="只看国内可直连" pressed={onlyCn} onClick={onToggleCn} />
          <span className="ml-1 shrink-0 font-mono text-[12px] text-ink-soft" aria-live="polite">
            {visibleCount}/{totalCount}
          </span>
        </div>
      </div>
    </header>
  )
}
