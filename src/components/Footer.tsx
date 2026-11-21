interface FooterProps {
  updated: string
  raw: number
}

export default function Footer({ updated, raw }: FooterProps) {
  return (
    <footer id="about" className="scroll-mt-6 border-t border-line pb-8 pt-8">
      <h2 className="mb-3 font-display text-[17px] font-semibold leading-6">说明</h2>
      <div className="max-w-2xl space-y-2.5 text-[13px] leading-5 text-ink-soft">
        <p>
          免费节点订阅索引站 · 每 3 小时自动采集 {raw.toLocaleString('zh-CN')} 条原始节点，按可连率
          × 节点数排序。更新于 {updated}。
        </p>
        <p>
          只做索引、不做聚合：所有链接原样指向各仓库发布地址，不合并、不去重、不转格式、不托管节点。
        </p>
        <p>
          本项目仅整理公开渠道的订阅链接，不生产、不存储、不修改任何节点资源。订阅内容来自第三方仓库，
          可用性与安全性由源作者负责。请遵守当地法律法规，仅作学习交流用途。
        </p>
      </div>
    </footer>
  )
}
