import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Printer,
  AlertCircle,
  Check,
  Store as StoreIcon,
  TrendingDown,
} from 'lucide-react'
import { getShoppingList } from '../api'
import type { ShoppingListResponse, ShoppingListEntry } from '../types'
import { formatCents } from '../types'

type Mode = 'single' | 'pair'

export default function ShoppingList() {
  const [mode, setMode] = useState<Mode>('single')

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['shopping-list', mode],
    queryFn: () => getShoppingList(mode),
  })

  return (
    <div className="p-4 sm:p-6 max-w-3xl mx-auto space-y-4">
      {/* Header — no-print */}
      <div className="flex items-center justify-between flex-wrap gap-3 no-print">
        <h1 className="text-2xl font-bold text-gray-900">Shopping List</h1>
        <div className="flex items-center gap-2">
          {/* Mode toggle */}
          <div className="inline-flex rounded-lg border border-gray-300 overflow-hidden">
            <button
              className={`px-3 py-1.5 text-sm font-medium ${mode === 'single' ? 'bg-deal-fresh text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
              onClick={() => setMode('single')}
            >
              Single store
            </button>
            <button
              className={`px-3 py-1.5 text-sm font-medium ${mode === 'pair' ? 'bg-deal-fresh text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
              onClick={() => setMode('pair')}
            >
              Two store
            </button>
          </div>

          {/* Print */}
          <button
            onClick={() => window.print()}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-gray-700 text-white rounded-lg text-sm font-medium hover:bg-gray-800"
          >
            <Printer size={16} /> Print
          </button>
        </div>
      </div>

      {isLoading && <Loading />}
      {isError && <ErrorBanner message={(error as Error).message} />}
      {data && <ListContent data={data} />}
    </div>
  )
}

/** Group entries by store for display. */
interface StoreGroup {
  store_id: number
  store_name: string
  items: ShoppingListEntry[]
  subtotal: number
}

function ListContent({ data }: { data: ShoppingListResponse }) {
  // Group entries by store_id for the store-grouped layout.
  const groups: StoreGroup[] = useMemo(() => {
    const map = new Map<number, StoreGroup>()
    for (const entry of data.entries) {
      let g = map.get(entry.store_id)
      if (!g) {
        g = { store_id: entry.store_id, store_name: entry.store_name, items: [], subtotal: 0 }
        map.set(entry.store_id, g)
      }
      g.items.push(entry)
      g.subtotal += entry.line_total
    }
    // Preserve store order from the response's store_ids
    const ordered = data.store_ids
      .map((id) => map.get(id))
      .filter((g): g is StoreGroup => Boolean(g))
    // Append any groups not in store_ids (defensive)
    for (const g of map.values()) {
      if (!ordered.includes(g)) ordered.push(g)
    }
    return ordered
  }, [data])

  return (
    <div className="space-y-4">
      {/* Summary bar */}
      <div className="bg-white rounded-xl border border-gray-200 p-4 flex items-center gap-6 flex-wrap printable">
        <div>
          <p className="text-xs text-gray-400 uppercase font-medium">Total cost</p>
          <p className="text-2xl font-bold text-gray-900">{formatCents(data.total_cost)}</p>
        </div>
        <div>
          <p className="text-xs text-gray-400 uppercase font-medium">Total savings</p>
          <p className="text-2xl font-bold text-deal-best flex items-center gap-1">
            <TrendingDown size={20} />
            {formatCents(data.savings)}
          </p>
        </div>
        <div className="ml-auto text-sm text-gray-400 capitalize">
          {data.mode === 'pair' ? 'Two-store trip' : 'Single-store trip'}
        </div>
      </div>

      {/* Store groups */}
      {groups.map((group) => (
        <StoreGroupCard key={group.store_id} group={group} />
      ))}

      {groups.length === 0 && (
        <div className="bg-white rounded-xl border p-10 text-center text-gray-500">
          <StoreIcon className="mx-auto text-gray-300" size={40} />
          <p className="mt-2">No items in your shopping list yet.</p>
          <p className="text-sm">Add items and refresh stores to find deals.</p>
        </div>
      )}
    </div>
  )
}

function StoreGroupCard({ group }: { group: StoreGroup }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden printable">
      {/* Header */}
      <div className="bg-gray-50 px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <StoreIcon size={18} className="text-gray-400" />
          <h2 className="font-semibold text-gray-800">{group.store_name}</h2>
          <span className="text-xs text-gray-400">{group.items.length} items</span>
        </div>
        <div className="text-right">
          <p className="text-xs text-gray-400">Subtotal</p>
          <p className="text-sm font-semibold">{formatCents(group.subtotal)}</p>
        </div>
      </div>

      {/* Items */}
      <ul className="divide-y divide-gray-100">
        {group.items.map((item) => (
          <ShoppingRow key={`${item.item_id}-${item.store_id}`} item={item} />
        ))}
      </ul>
    </div>
  )
}

function ShoppingRow({ item }: { item: ShoppingListEntry }) {
  const [checked, setChecked] = useState(false)
  return (
    <li
      className={`flex items-center gap-3 px-4 py-3 ${checked ? 'bg-gray-50 opacity-60' : ''}`}
    >
      <button
        onClick={() => setChecked(!checked)}
        className={`shrink-0 w-5 h-5 rounded border-2 flex items-center justify-center transition-colors no-print
        ${checked ? 'bg-deal-fresh border-deal-fresh text-white' : 'border-gray-300 hover:border-deal-fresh'}`}
        aria-label={checked ? 'Uncheck item' : 'Check item'}
      >
        {checked && <Check size={14} />}
      </button>
      {/* Print-only checkbox */}
      <span className="hidden print:inline-block w-5 h-5 border-2 border-gray-400 rounded mr-0" />
      <div className="min-w-0 flex-1">
        <p className={`text-sm font-medium text-gray-900 ${checked ? 'line-through' : ''}`}>
          {item.item_name}
        </p>
        <p className="text-xs text-gray-400 truncate">
          {item.deal_type || (item.is_sale ? 'Sale' : '')}
        </p>
      </div>
      <span className="text-sm font-semibold text-gray-700 shrink-0">
        {formatCents(item.line_total)}
      </span>
    </li>
  )
}

function Loading() {
  return (
    <div className="animate-pulse space-y-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="h-24 bg-gray-200 rounded-xl" />
      ))}
    </div>
  )
}
function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-600 flex items-center gap-2 no-print">
      <AlertCircle size={16} /> {message}
    </div>
  )
}
