interface ClientLink {
  name: string
  url: string
}

interface ClientRow {
  key: string
  links: ClientLink[]
}

const CLIENTS: ClientRow[] = [
  {
    key: '电脑',
    links: [
      { name: 'Clash Verge Rev', url: 'https://github.com/clash-verge-rev/clash-verge-rev' },
      { name: 'v2rayN', url: 'https://github.com/2dust/v2rayN' },
      { name: 'ClashX.Meta', url: 'https://github.com/MetaCubeX/ClashX.Meta' },
    ],
  },
  {
    key: '安卓',
    links: [
      { name: 'v2rayNG', url: 'https://github.com/2dust/v2rayNG' },
      { name: 'FlClash', url: 'https://github.com/chen08209/FlClash' },
    ],
  },
  {
    key: 'iOS',
    links: [{ name: 'sing-box', url: 'https://github.com/SagerNet/sing-box' }],
  },
  {
    key: '全平台',
    links: [
      { name: 'Hiddify', url: 'https://github.com/hiddify/hiddify-app' },
      { name: 'Karing', url: 'https://github.com/KaringX/karing' },
    ],
  },
]

export default function ClientSection() {
  return (
    <section id="clients" className="mb-11 scroll-mt-6">
      <h2 className="mb-4 font-display text-[17px] font-semibold leading-6">主流客户端下载</h2>
      <div className="rounded-xl bg-surface p-5 shadow-1">
        {CLIENTS.map((row) => (
          <div
            key={row.key}
            className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line py-3 first:pt-0 last:border-b-0 last:pb-0"
          >
            <span className="w-12 shrink-0 text-[13px] font-medium text-ink-soft">{row.key}</span>
            <div className="flex flex-wrap gap-2">
              {row.links.map((link) => (
                <a
                  key={link.name}
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-full border border-line bg-raised px-3 py-1 text-[13px] text-ink transition-colors duration-200 hover:bg-surface hover:text-ink"
                >
                  {link.name}
                </a>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
