import { useEffect } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { X, Copy } from 'lucide-react'
import type { SourceItem } from '../types'

interface QrModalProps {
  item: SourceItem | null
  onClose: () => void
  onCopy: (item: SourceItem) => void
}

export default function QrModal({ item, onClose, onCopy }: QrModalProps) {
  useEffect(() => {
    if (!item) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [item, onClose])

  if (!item) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`${item.tag} 订阅二维码`}
    >
      <div
        className="absolute inset-0 bg-ink/30 backdrop-blur-[2px]"
        onClick={onClose}
        aria-hidden="true"
      />
      <div className="relative w-full max-w-sm rounded-xl bg-raised p-6 shadow-3 animate-rise">
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭"
          className="absolute right-4 top-4 flex h-8 w-8 items-center justify-center rounded-full text-ink-soft transition-colors duration-200 hover:bg-surface hover:text-ink"
        >
          <X size={16} strokeWidth={1.5} />
        </button>

        <h3 className="pr-8 font-display text-[15px] font-semibold">{item.tag}</h3>

        <div className="mx-auto mt-5 flex h-52 w-52 items-center justify-center rounded-lg border border-line bg-white p-3">
          <QRCodeSVG value={item.url} size={184} level="M" marginSize={0} />
        </div>

        <p className="mt-4 break-all rounded-md bg-surface px-3 py-2.5 font-mono text-[11px] leading-4 text-ink-soft">
          {item.url}
        </p>

        <button
          type="button"
          onClick={() => onCopy(item)}
          className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-full bg-ink px-4 py-2.5 text-[13px] font-medium text-raised transition-opacity duration-200 hover:opacity-85"
        >
          <Copy size={15} strokeWidth={1.5} />
          复制链接
        </button>
      </div>
    </div>
  )
}
