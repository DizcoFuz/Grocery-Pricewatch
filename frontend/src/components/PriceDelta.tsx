import { ArrowDown, ArrowUp, Minus, Sparkles } from 'lucide-react'
import { formatDeltaCents } from '../types'

interface PriceDeltaProps {
  /** Delta in cents: negative = cheaper (better), positive = more expensive (worse). */
  deltaCents: number | null
  /** Direction classification from backend. */
  direction: 'better' | 'worse' | 'unchanged' | 'new'
  /** Optional explicit label override. */
  label?: string
}

/**
 * Shows a price delta with color coding:
 *  - Better: green + down arrow
 *  - Worse: red + up arrow
 *  - Unchanged: neutral
 *  - First ever: "new" badge
 */
export default function PriceDelta({ deltaCents, direction, label }: PriceDeltaProps) {
  if (direction === 'new') {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-semibold text-deal-new">
        <Sparkles size={12} />
        new
      </span>
    )
  }

  if (direction === 'unchanged' || deltaCents === null || deltaCents === 0) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-gray-400">
        <Minus size={12} />
        {label ?? 'same'}
      </span>
    )
  }

  if (direction === 'better') {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-semibold text-deal-best">
        <ArrowDown size={12} />
        {label ?? formatDeltaCents(deltaCents)}
      </span>
    )
  }

  // worse
  return (
    <span className="inline-flex items-center gap-1 text-xs font-semibold text-deal-bad">
      <ArrowUp size={12} />
      {label ?? formatDeltaCents(deltaCents)}
    </span>
  )
}
