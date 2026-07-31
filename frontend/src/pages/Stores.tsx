import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  RefreshCw,
  ChevronDown,
  ChevronRight,
  AlertCircle,
  Store as StoreIcon,
} from 'lucide-react'
import { getStores, updateStore, refreshStore } from '../api'
import type { StoreRead, StoreUpdate } from '../types'
import { relativeTime } from '../types'
import StoreStatusBadge from '../components/StoreStatusBadge'

export default function Stores() {
  const qc = useQueryClient()
  const [editingId, setEditingId] = useState<number | null>(null)
  const [expandedLog, setExpandedLog] = useState<number | null>(null)
  const [refreshing, setRefreshing] = useState<number | null>(null)

  const { data: stores, isLoading, isError, error } = useQuery({
    queryKey: ['stores'],
    queryFn: getStores,
  })

  const updateMut = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: StoreUpdate }) => updateStore(id, patch),
    onSettled: () => qc.invalidateQueries({ queryKey: ['stores'] }),
  })

  const refreshMut = useMutation({
    mutationFn: (id: number) => refreshStore(id),
    onMutate: (id) => setRefreshing(id),
    onSettled: () => {
      setRefreshing(null)
      qc.invalidateQueries({ queryKey: ['stores'] })
      qc.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })

  if (isLoading) return <Loading />
  if (isError) return <ErrorBanner message={(error as Error).message} />
  if (!stores) return null

  return (
    <div className="p-4 sm:p-6 max-w-5xl mx-auto space-y-4">
      <h1 className="text-2xl font-bold text-gray-900">Stores</h1>
      <p className="text-sm text-gray-500 -mt-2">
        Enable stores, set ZIP/store IDs, and fetch ads.
      </p>

      <div className="space-y-3">
        {stores.map((store) => (
          <StoreCard
            key={store.id}
            store={store}
            editing={editingId === store.id}
            onEditToggle={() => setEditingId(editingId === store.id ? null : store.id)}
            onSave={(patch) => {
              updateMut.mutate({ id: store.id, patch })
              setEditingId(null)
            }}
            saving={updateMut.isPending}
            onRefresh={() => refreshMut.mutate(store.id)}
            refreshing={refreshing === store.id}
            logExpanded={expandedLog === store.id}
            onLogToggle={() => setExpandedLog(expandedLog === store.id ? null : store.id)}
          />
        ))}
        {stores.length === 0 && (
          <div className="bg-white rounded-xl border p-10 text-center text-gray-500">
            <StoreIcon className="mx-auto text-gray-300" size={40} />
            <p className="mt-2">No stores configured.</p>
          </div>
        )}
      </div>

      {refreshMut.isError && (
        <div className="text-sm text-red-600 flex items-center gap-2">
          <AlertCircle size={16} /> Refresh failed: {(refreshMut.error as Error).message}
        </div>
      )}
    </div>
  )
}

function StoreCard({
  store, editing, onEditToggle, onSave, saving,
  onRefresh, refreshing, logExpanded, onLogToggle,
}: {
  store: StoreRead
  editing: boolean
  onEditToggle: () => void
  onSave: (patch: StoreUpdate) => void
  saving: boolean
  onRefresh: () => void
  refreshing: boolean
  logExpanded: boolean
  onLogToggle: () => void
}) {
  const [enabled, setEnabled] = useState(store.enabled)
  const [zipOrStoreId, setZipOrStoreId] = useState(store.zip_or_store_id ?? '')
  const [name, setName] = useState(store.name)
  const [adapterKey, setAdapterKey] = useState(store.adapter_key)

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="p-4 flex items-center gap-4 flex-wrap">
        {/* Enable toggle */}
        <button
          role="switch"
          aria-checked={enabled}
          onClick={() => {
            const next = !enabled
            setEnabled(next)
            onSave({ enabled: next })
          }}
          className={`relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors ${enabled ? 'bg-deal-fresh' : 'bg-gray-300'}`}
        >
          <span
            className={`inline-block h-5 w-5 rounded-full bg-white shadow transform transition-transform mt-0.5 ${enabled ? 'translate-x-5' : 'translate-x-0.5'}`}
          />
        </button>

        {/* Name + adapter */}
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-gray-900">{store.name}</p>
          <p className="text-xs text-gray-400 uppercase tracking-wide">{store.adapter_key}</p>
        </div>

        {/* Status badge */}
        <StoreStatusBadge status={store.last_fetch_status} lastFetchAt={store.last_fetch_at} />

        {/* Last fetch */}
        <div className="text-xs text-gray-400 text-right">
          <p>{relativeTime(store.last_fetch_at)}</p>
        </div>

        {/* Refresh */}
        <button
          onClick={onRefresh}
          disabled={refreshing || !enabled}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-gray-100 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-200 disabled:opacity-40 disabled:cursor-not-allowed"
          title={enabled ? 'Refresh this store' : 'Store is disabled'}
        >
          <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          {refreshing ? 'Fetching…' : 'Refresh'}
        </button>

        {/* Expand logs */}
        <button
          onClick={onLogToggle}
          className="p-1.5 text-gray-400 hover:text-gray-600 rounded"
          title="View fetch details"
        >
          {logExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
      </div>

      {/* Editable fields */}
      {editing && (
        <div className="px-4 pb-4 border-t border-gray-100 pt-3 grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Name</label>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Store name"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Adapter key</label>
            <input
              className="input"
              value={adapterKey}
              onChange={(e) => setAdapterKey(e.target.value)}
              placeholder="e.g. kroger, safeway"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">ZIP / Store ID</label>
            <input
              className="input"
              value={zipOrStoreId}
              onChange={(e) => setZipOrStoreId(e.target.value)}
              placeholder="ZIP code or store-specific ID"
            />
          </div>
          <div className="sm:col-span-2 flex justify-end gap-2">
            <button onClick={onEditToggle} className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">
              Cancel
            </button>
            <button
              onClick={() => onSave({
                name: name || null,
                adapter_key: adapterKey || null,
                zip_or_store_id: zipOrStoreId,
              })}
              disabled={saving}
              className="px-3 py-1.5 bg-deal-fresh text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      )}

      {/* Fetch details */}
      {logExpanded && (
        <div className="px-4 pb-4 border-t border-gray-100 pt-3">
          <p className="text-xs font-medium text-gray-500 mb-1">Last fetch status</p>
          {store.last_fetch_status && (
            <div className="text-sm text-gray-700 bg-gray-50 border border-gray-200 rounded p-2 mb-2">
              {store.last_fetch_status}
            </div>
          )}
          {!store.last_fetch_status && (
            <pre className="text-xs text-gray-600 bg-gray-50 rounded-lg p-3 overflow-x-auto max-h-48 whitespace-pre-wrap font-mono">
              No fetch has been run yet.
            </pre>
          )}
          <p className="text-xs text-gray-400 mt-2">
            Last fetch: {relativeTime(store.last_fetch_at)}
          </p>
        </div>
      )}
    </div>
  )
}

function Loading() {
  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="animate-pulse space-y-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-20 bg-gray-200 rounded-xl" />
        ))}
      </div>
    </div>
  )
}
function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-600 flex items-center gap-2">
        <AlertCircle size={16} /> {message}
      </div>
    </div>
  )
}
