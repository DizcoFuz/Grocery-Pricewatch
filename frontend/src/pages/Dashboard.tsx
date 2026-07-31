import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  RefreshCw,
  TrendingDown,
  Store as StoreIcon,
  AlertCircle,
  ClipboardCheck,
  ArrowRight,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { getDashboard, refreshAll } from '../api'
import type { DashboardResponse, DashboardBestDeal, StoreStatus as StoreStatusRow } from '../types'
import { formatCents, relativeTime } from '../types'
import StoreStatusBadge from '../components/StoreStatusBadge'

export default function Dashboard() {
  const qc = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['dashboard'],
    queryFn: getDashboard,
  })

  const refreshMut = useMutation({
    mutationFn: refreshAll,
    onMutate: () => setRefreshing(true),
    onSettled: () => {
      setRefreshing(false)
      qc.invalidateQueries({ queryKey: ['dashboard'] })
      qc.invalidateQueries({ queryKey: ['review-queue'] })
      qc.invalidateQueries({ queryKey: ['recommendations'] })
    },
  })

  if (isLoading) return <LoadingState />
  if (isError) return <ErrorState message={(error as Error).message} onRetry={() => refetch()} />
  if (!data) return null

  // Derive a "last refresh" time from the most recent store fetch.
  const fetchTimes = data.store_statuses
    .map((s) => s.last_fetch_at)
    .filter((v): v is string => Boolean(v))
    .sort()
  const lastFetch = fetchTimes.length > 0 ? fetchTimes[fetchTimes.length - 1] : undefined

  return (
    <div className="p-4 sm:p-6 max-w-7xl mx-auto space-y-6">
      {/* Header row */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
          <p className="text-sm text-gray-500">
            Last refresh: {relativeTime(lastFetch)}
          </p>
        </div>
        <button
          className="inline-flex items-center gap-2 px-4 py-2 bg-deal-fresh text-white rounded-lg font-medium hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          onClick={() => refreshMut.mutate()}
          disabled={refreshing}
        >
          <RefreshCw size={18} className={refreshing ? 'animate-spin' : ''} />
          {refreshing ? 'Refreshing…' : 'Refresh now'}
        </button>
      </div>

      {/* Review queue badge */}
      {data.review_queue_count > 0 && (
        <Link
          to="/review-queue"
          className="block no-print"
        >
          <div className="flex items-center gap-3 px-4 py-3 bg-amber-50 border border-amber-200 rounded-lg hover:bg-amber-100 transition-colors">
            <ClipboardCheck className="text-amber-600 shrink-0" size={20} />
            <span className="text-sm font-medium text-amber-800">
              {data.review_queue_count} uncertain {data.review_queue_count === 1 ? 'match' : 'matches'} need your review
            </span>
            <ArrowRight size={16} className="text-amber-600 ml-auto" />
          </div>
        </Link>
      )}

      {/* Headline savings card */}
      <HeadlineCard data={data} />

      {/* Store fetch status strip */}
      <StoreStatusStrip data={data} />

      {/* Best-deals table */}
      <BestDealsTable data={data} />

      {refreshMut.isError && (
        <div className="flex items-center gap-2 text-sm text-red-600">
          <AlertCircle size={16} />
          Refresh failed: {(refreshMut.error as Error).message}
        </div>
      )}
    </div>
  )
}

// ── Headline card ──────────────────────────────────────────────
function HeadlineCard({ data }: { data: DashboardResponse }) {
  return (
    <div className="bg-gradient-to-br from-green-600 to-green-800 text-white rounded-2xl p-6 shadow-lg">
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div>
          <p className="text-green-100 text-sm font-medium uppercase tracking-wide">
            Projected savings this week
          </p>
          <p className="text-4xl sm:text-5xl font-bold mt-1">
            {formatCents(data.headline_savings)}
          </p>
          <p className="text-green-100 text-sm mt-2">
            {data.last_report ? `Week of ${data.last_report.week}` : 'No report yet'}
          </p>
        </div>
        <div className="text-right">
          <p className="text-green-100 text-sm font-medium uppercase tracking-wide">
            Recommended
          </p>
          <p className="text-xl font-bold mt-1">
            {data.headline_store || '—'}
          </p>
          <p className="text-green-100 text-sm mt-1 capitalize">
            {data.headline_mode === 'pair' ? 'Two-store trip' : 'Single-store trip'}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2 mt-4 text-green-100 text-sm">
        <TrendingDown size={16} />
        Best prices found this ad cycle
      </div>
    </div>
  )
}

// ── Store status strip ─────────────────────────────────────────
function StoreStatusStrip({ data }: { data: DashboardResponse }) {
  return (
    <div>
      <h2 className="text-sm font-semibold text-gray-600 mb-2 uppercase tracking-wide">
        Store fetch status
      </h2>
      <div className="flex flex-wrap gap-2">
        {data.store_statuses.map((s: StoreStatusRow) => (
          <div
            key={s.store_id}
            className="flex items-center gap-3 bg-white border border-gray-200 rounded-lg px-3 py-2 min-w-[180px]"
          >
            <StoreStatusBadge status={s.last_fetch_status} lastFetchAt={s.last_fetch_at} size="sm" />
            <div className="min-w-0">
              <p className="text-sm font-medium text-gray-800 truncate">{s.name}</p>
              <p className="text-xs text-gray-400">
                {relativeTime(s.last_fetch_at)}
              </p>
            </div>
            <span className="ml-auto text-xs text-gray-400">
              {s.enabled ? 'on' : 'off'}
            </span>
          </div>
        ))}
        {data.store_statuses.length === 0 && (
          <p className="text-sm text-gray-400">No stores configured.</p>
        )}
      </div>
    </div>
  )
}

// ── Best-deals table ────────────────────────────────────────────
function BestDealsTable({ data }: { data: DashboardResponse }) {
  if (data.best_deals.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
        <StoreIcon className="mx-auto text-gray-300" size={40} />
        <p className="mt-3 text-gray-500">No deals matched yet. Try refreshing.</p>
      </div>
    )
  }

  return (
    <div>
      <h2 className="text-sm font-semibold text-gray-600 mb-2 uppercase tracking-wide">
        Best deals
      </h2>
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <Th>Item</Th>
                <Th className="text-right">Sale price</Th>
                <Th>Store</Th>
                <Th className="text-right">Unit price</Th>
                <Th>Deal</Th>
                <Th className="text-right">Savings</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.best_deals.map((row: DashboardBestDeal, i: number) => (
                <tr key={`${row.item_name}-${i}`} className="hover:bg-gray-50">
                  <Td>
                    <div className="font-medium text-gray-900">{row.item_name}</div>
                  </Td>
                  <Td className="text-right">
                    <span className="font-semibold text-gray-900">
                      {formatCents(row.sale_price)}
                    </span>
                  </Td>
                  <Td>
                    <span className="text-sm text-gray-700">{row.store_name}</span>
                  </Td>
                  <Td className="text-right">
                    <span className="text-sm text-gray-600">{formatCents(row.unit_price)}</span>
                  </Td>
                  <Td>
                    <span className="text-xs text-gray-500 line-clamp-2 max-w-xs">
                      {row.deal_type || '—'}
                    </span>
                  </Td>
                  <Td className="text-right">
                    <span className="text-xs font-semibold text-deal-best">
                      {formatCents(row.savings_vs_baseline)}
                    </span>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ── Helpers / sub-components ───────────────────────────────────
function Th({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <th
      className={`px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider ${className}`}
    >
      {children}
    </th>
  )
}

function Td({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-4 py-3 ${className}`}>{children}</td>
}

function LoadingState() {
  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="animate-pulse space-y-6">
        <div className="h-8 w-40 bg-gray-200 rounded" />
        <div className="h-40 bg-gray-200 rounded-2xl" />
        <div className="h-24 bg-gray-200 rounded-xl" />
        <div className="h-64 bg-gray-200 rounded-xl" />
      </div>
    </div>
  )
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-center">
        <AlertCircle className="mx-auto text-red-500" size={32} />
        <p className="mt-3 text-red-700 font-medium">Failed to load dashboard</p>
        <p className="text-sm text-red-500 mt-1">{message}</p>
        <button
          onClick={onRetry}
          className="mt-4 px-4 py-2 bg-red-600 text-white rounded-lg text-sm font-medium hover:bg-red-700"
        >
          Retry
        </button>
      </div>
    </div>
  )
}
