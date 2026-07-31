import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Check,
  X,
  AlertCircle,
  ClipboardCheck,
  Loader2,
} from 'lucide-react'
import { getReviewQueue, decideMatch } from '../api'
import type { MatchWithDetails } from '../types'
import { formatCents } from '../types'

export default function ReviewQueue() {
  const qc = useQueryClient()

  const { data: entries, isLoading, isError, error } = useQuery({
    queryKey: ['review-queue'],
    queryFn: getReviewQueue,
  })

  const decideMut = useMutation({
    mutationFn: ({ id, decision }: { id: number; decision: 'accept' | 'reject' }) =>
      decideMatch(id, decision),
    onSettled: () => {
      // Immediately invalidate so best prices & recommendations update
      qc.invalidateQueries({ queryKey: ['review-queue'] })
      qc.invalidateQueries({ queryKey: ['dashboard'] })
      qc.invalidateQueries({ queryKey: ['recommendations'] })
      qc.invalidateQueries({ queryKey: ['shopping-list'] })
    },
  })

  return (
    <div className="p-4 sm:p-6 max-w-4xl mx-auto space-y-4">
      <div className="flex items-center gap-2">
        <ClipboardCheck className="text-amber-500" size={24} />
        <h1 className="text-2xl font-bold text-gray-900">Review Queue</h1>
      </div>

      {isLoading && <Loading />}
      {isError && <ErrorBanner message={(error as Error).message} />}

      {entries && entries.length === 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-10 text-center">
          <Check className="mx-auto text-deal-fresh" size={40} />
          <p className="mt-3 text-gray-600 font-medium">All caught up!</p>
          <p className="text-sm text-gray-400">No uncertain matches need review.</p>
        </div>
      )}

      {entries && entries.length > 0 && (
        <>
          <p className="text-sm text-gray-500">
            {entries.length} {entries.length === 1 ? 'match' : 'matches'} below the confidence threshold.
            Accept to use the price, or reject to discard.
          </p>
          <div className="space-y-3">
            {entries.map((entry) => (
              <ReviewCard
                key={entry.id}
                entry={entry}
                deciding={decideMut.isPending && decideMut.variables?.id === entry.id}
                onAccept={() => decideMut.mutate({ id: entry.id, decision: 'accept' })}
                onReject={() => decideMut.mutate({ id: entry.id, decision: 'reject' })}
              />
            ))}
          </div>
        </>
      )}

      {decideMut.isError && (
        <div className="text-sm text-red-600 flex items-center gap-2">
          <AlertCircle size={16} /> Decision failed: {(decideMut.error as Error).message}
        </div>
      )}
    </div>
  )
}

function ReviewCard({
  entry, deciding, onAccept, onReject,
}: {
  entry: MatchWithDetails
  deciding: boolean
  onAccept: () => void
  onReject: () => void
}) {
  const pct = Math.round(entry.confidence * 100)
  const confColor =
    pct >= 75 ? 'text-deal-best' : pct >= 50 ? 'text-amber-600' : 'text-deal-bad'

  const offerPrice = entry.offer.price

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        {/* Offer text + matched item */}
        <div className="min-w-0 flex-1">
          <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">
            Offer from {entry.store_name}
          </p>
          <p className="text-sm font-medium text-gray-900 mb-2">"{entry.offer.raw_text}"</p>
          {entry.offer.product_name && (
            <p className="text-xs text-gray-500 mb-1">
              {entry.offer.product_name}
              {entry.offer.brand ? ` — ${entry.offer.brand}` : ''}
              {entry.offer.size_text ? ` (${entry.offer.size_text})` : ''}
            </p>
          )}
          <div className="flex items-center gap-4 flex-wrap text-sm">
            <div>
              <span className="text-gray-400">Matched to: </span>
              <span className="text-gray-700 font-medium">{entry.item_name}</span>
            </div>
            <div>
              <span className="text-gray-400">Price: </span>
              <span className="text-gray-900 font-semibold">{formatCents(offerPrice)}</span>
            </div>
            {entry.offer.deal_type && (
              <div>
                <span className="text-gray-400">Deal: </span>
                <span className="text-gray-700">{entry.offer.deal_type}</span>
              </div>
            )}
          </div>
        </div>

        {/* Confidence */}
        <div className="text-right shrink-0">
          <p className="text-xs text-gray-400 uppercase">Confidence</p>
          <p className={`text-2xl font-bold ${confColor}`}>{pct}%</p>
        </div>
      </div>

      {/* Confidence bar */}
      <div className="mt-3 h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${pct >= 75 ? 'bg-deal-best' : pct >= 50 ? 'bg-amber-500' : 'bg-deal-bad'}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Actions */}
      <div className="flex justify-end gap-2 mt-3 no-print">
        <button
          onClick={onReject}
          disabled={deciding}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-red-50 text-deal-bad border border-red-200 rounded-lg text-sm font-medium hover:bg-red-100 disabled:opacity-50"
        >
          {deciding ? <Loader2 size={14} className="animate-spin" /> : <X size={14} />}
          Reject
        </button>
        <button
          onClick={onAccept}
          disabled={deciding}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-green-50 text-deal-fresh border border-green-200 rounded-lg text-sm font-medium hover:bg-green-100 disabled:opacity-50"
        >
          {deciding ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
          Accept
        </button>
      </div>
    </div>
  )
}

function Loading() {
  return (
    <div className="animate-pulse space-y-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="h-28 bg-gray-200 rounded-xl" />
      ))}
    </div>
  )
}
function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-600 flex items-center gap-2">
      <AlertCircle size={16} /> {message}
    </div>
  )
}
