import { useCallback, useEffect, useMemo, useState } from 'react'
import type { NodesData, SourceItem } from './types'
import { FALLBACK_DATA } from './data/display'
import { connectivityRate } from './lib/utils'
import IconSidebar from './components/IconSidebar'
import TopBar from './components/TopBar'
import GroupSection from './components/GroupSection'
import QrModal from './components/QrModal'
import Toast from './components/Toast'
import ClientSection from './components/ClientSection'
import Footer from './components/Footer'

const SECTION_IDS = ['overview', 'sources', 'clients', 'about']

export default function App() {
  const [data, setData] = useState<NodesData>(FALLBACK_DATA)
  const [query, setQuery] = useState('')
  const [onlyAlive, setOnlyAlive] = useState(false)
  const [onlyCn, setOnlyCn] = useState(false)
  const [active, setActive] = useState('overview')
  const [qrItem, setQrItem] = useState<SourceItem | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  // 运行时优先拉取最新采集数据，失败则回退内嵌数据
  useEffect(() => {
    let cancelled = false
    fetch('data/display.json')
      .then((r) => (r.ok ? (r.json() as Promise<NodesData>) : null))
      .then((d) => {
        if (!cancelled && d) setData(d)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // 侧边栏滚动联动
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) setActive(e.target.id)
        }
      },
      { rootMargin: '-40% 0px -55% 0px' },
    )
    for (const id of SECTION_IDS) {
      const el = document.getElementById(id)
      if (el) observer.observe(el)
    }
    return () => observer.disconnect()
  }, [])

  const showToast = useCallback((msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast(null), 1800)
  }, [])

  const copyText = useCallback(
    async (text: string, msg: string) => {
      try {
        await navigator.clipboard.writeText(text)
      } catch {
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        ta.remove()
      }
      showToast(msg)
    },
    [showToast],
  )

  const handleCopy = useCallback(
    (item: SourceItem) => copyText(item.url, '已复制订阅链接'),
    [copyText],
  )

  const handleNavigate = useCallback((id: string) => {
    setActive(id)
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [])

  const filteredGroups = useMemo(() => {
    const q = query.trim().toLowerCase()
    return data.groups
      .map((group) => {
        const items = group.items
          .filter((item) => {
            if (onlyAlive && connectivityRate(item) < 0.8) return false
            if (onlyCn && item.cnbad) return false
            if (q) {
              const hay = `${item.tag} ${item.note} ${item.url} ${item.freq}`.toLowerCase()
              if (!hay.includes(q)) return false
            }
            return true
          })
          .sort((a, b) => b.score - a.score)
        return { ...group, items }
      })
      .filter((g) => g.items.length > 0)
  }, [data, query, onlyAlive, onlyCn])

  const visibleCount = useMemo(
    () => filteredGroups.reduce((n, g) => n + g.items.length, 0),
    [filteredGroups],
  )

  const totalCount = useMemo(
    () => data.groups.reduce((n, g) => n + g.items.length, 0),
    [data],
  )

  const highlight = useMemo(() => {
    const set = new Set<string>()
    for (const g of data.groups) {
      const top = [...g.items].sort((a, b) => b.score - a.score)[0]
      if (top) set.add(top.url)
    }
    return set
  }, [data])

  const copyAll = useCallback(() => {
    const urls = filteredGroups.flatMap((g) => g.items.map((i) => i.url))
    if (urls.length === 0) return
    copyText(urls.join('\n'), `已复制 ${urls.length} 条链接`)
  }, [filteredGroups, copyText])

  return (
    <div className="min-h-screen">
      <IconSidebar active={active} onNavigate={handleNavigate} />

      <main className="md:pl-16">
        <div className="mx-auto max-w-6xl px-5 py-8 md:px-8 lg:px-12">
          <div id="overview" className="scroll-mt-6">
            <TopBar
              query={query}
              onQueryChange={setQuery}
              onlyAlive={onlyAlive}
              onToggleAlive={() => setOnlyAlive((v) => !v)}
              onlyCn={onlyCn}
              onToggleCn={() => setOnlyCn((v) => !v)}
              visibleCount={visibleCount}
              totalCount={totalCount}
              updated={data.updated}
              onCopyAll={copyAll}
            />
          </div>

          {visibleCount === 0 ? (
            <div className="rounded-xl bg-surface p-12 text-center shadow-1">
              <p className="font-display text-[15px] font-semibold">没有匹配的订阅源</p>
              <p className="mt-1.5 text-[13px] text-ink-soft">试试清空搜索或关闭筛选条件。</p>
            </div>
          ) : (
            <div id="sources" className="scroll-mt-6">
              {filteredGroups.map((group, i) => (
                <GroupSection
                  key={group.title}
                  group={group}
                  index={i}
                  highlight={highlight}
                  onCopy={handleCopy}
                  onQr={setQrItem}
                />
              ))}
            </div>
          )}

          <ClientSection />
          <Footer updated={data.updated} raw={data.raw} />
        </div>
      </main>

      <QrModal item={qrItem} onClose={() => setQrItem(null)} onCopy={handleCopy} />
      <Toast message={toast} />
    </div>
  )
}
