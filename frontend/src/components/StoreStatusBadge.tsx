import { CheckCircle, AlertTriangle, XCircle, Clock, HelpCircle } from 'lucide-react'
import { relativeTime } from '../types'

interface StoreStatusBadgeProps {
  /** Free-form status string from the backend (may be null when no fetch has run). */
  status: string | null | undefined
  lastFetchAt?: string | null
  size?: 'sm' | 'md'
}

type BadgeConfig = { label: string; bg: string; text: string; Icon: typeof CheckCircle }

const config: Record<string, BadgeConfig> = {
  fresh: { label: 'Fresh', bg: 'bg-green-100', text: 'text-deal-fresh', Icon: CheckCircle },
  ok: { label: 'OK', bg: 'bg-green-100', text: 'text-deal-fresh', Icon: CheckCircle },
  success: { label: 'Success', bg: 'bg-green-100', text: 'text-deal-fresh', Icon: CheckCircle },
  stale: { label: 'Stale', bg: 'bg-yellow-100', text: 'text-deal-stale', Icon: Clock },
  failed: { label: 'Failed', bg: 'bg-red-100', text: 'text-deal-failed', Icon: XCircle },
  error: { label: 'Error', bg: 'bg-red-100', text: 'text-deal-failed', Icon: XCircle },
  partial: { label: 'Partial', bg: 'bg-orange-100', text: 'text-deal-partial', Icon: AlertTriangle },
}

const unknownConfig: BadgeConfig = {
  label: 'Unknown',
  bg: 'bg-gray-100',
  text: 'text-gray-500',
  Icon: HelpCircle,
}

function resolveConfig(status: string | null | undefined): BadgeConfig {
  if (!status) return unknownConfig
  const key = status.toLowerCase().trim()
  return config[key] ?? { ...unknownConfig, label: status }
}

export default function StoreStatusBadge({
  status,
  lastFetchAt,
  size = 'md',
}: StoreStatusBadgeProps) {
  const c = resolveConfig(status)
  const Icon = c.Icon
  const pad = size === 'sm' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-1 text-xs'

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full font-semibold ${c.bg} ${c.text} ${pad}`}
      title={lastFetchAt ? `Last fetch ${relativeTime(lastFetchAt)}` : undefined}
    >
      <Icon size={size === 'sm' ? 10 : 12} />
      {c.label}
    </span>
  )
}
