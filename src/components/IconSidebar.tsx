import { Sparkle, LayoutGrid, Rss, Download, Info } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

function GithubMark({ size = 19 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4" />
      <path d="M9 18c-4.51 2-5-2-7-2" />
    </svg>
  )
}

interface NavItem {
  id: string
  label: string
  icon: LucideIcon
}

const NAV: NavItem[] = [
  { id: 'overview', label: '概览', icon: LayoutGrid },
  { id: 'sources', label: '订阅源', icon: Rss },
  { id: 'clients', label: '客户端', icon: Download },
  { id: 'about', label: '说明', icon: Info },
]

interface IconSidebarProps {
  active: string
  onNavigate: (id: string) => void
}

export default function IconSidebar({ active, onNavigate }: IconSidebarProps) {
  return (
    <aside className="fixed inset-y-0 left-0 z-30 hidden w-16 flex-col items-center border-r border-line bg-surface py-5 md:flex">
      {/* Logo */}
      <button
        type="button"
        aria-label="返回顶部"
        onClick={() => onNavigate('overview')}
        className="flex h-9 w-9 items-center justify-center rounded-xl bg-ink text-raised shadow-1 transition-opacity hover:opacity-85"
      >
        <Sparkle size={18} strokeWidth={1.75} />
      </button>

      {/* 主导航 */}
      <nav className="mt-8 flex flex-col items-center gap-1.5" aria-label="主导航">
        {NAV.map(({ id, label, icon: Icon }) => {
          const isActive = active === id
          return (
            <button
              key={id}
              type="button"
              aria-label={label}
              aria-current={isActive ? 'page' : undefined}
              onClick={() => onNavigate(id)}
              className={`flex h-10 w-10 items-center justify-center rounded-xl transition-colors duration-200 ${
                isActive
                  ? 'bg-ink text-raised shadow-1'
                  : 'text-ink-soft hover:bg-raised hover:text-ink'
              }`}
            >
              <Icon size={19} strokeWidth={1.5} />
            </button>
          )
        })}
      </nav>

      {/* 垂直装饰线 */}
      <div className="relative my-auto h-24 w-px bg-line" aria-hidden="true">
        <span className="absolute left-1/2 top-1/2 h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-ink-soft" />
      </div>

      {/* 底部工具 */}
      <a
        href="https://github.com/BOB0098/nodes"
        target="_blank"
        rel="noopener noreferrer"
        aria-label="GitHub 仓库"
        className="flex h-10 w-10 items-center justify-center rounded-xl text-ink-soft transition-colors duration-200 hover:bg-raised hover:text-ink"
      >
        <GithubMark size={19} />
      </a>
    </aside>
  )
}
