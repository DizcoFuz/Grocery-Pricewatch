import { useState, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plus,
  Pencil,
  Trash2,
  Download,
  Upload,
  FileSpreadsheet,
  ChevronDown,
  ChevronRight,
  X,
  AlertCircle,
  FileDown,
} from 'lucide-react'
import {
  getItems,
  createItem,
  updateItem,
  deleteItem,
  importItemsCsv,
  exportItemsCsv,
  exportItemsJson,
  downloadTemplateCsv,
  downloadTemplateJson,
  getItemHistory,
} from '../api'
import type { ItemRead, ItemCreate, ItemUpdate, ItemImportResult, PriceHistoryRead } from '../types'
import { formatCents } from '../types'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'

const CSV_TYPES = ['csv']
const JSON_TYPES = ['json']

function isCsvFile(file: File): boolean {
  return file.name.toLowerCase().endsWith('.csv')
}

function joinList(list: string[] | undefined | null): string {
  if (!list || list.length === 0) return ''
  return list.join(', ')
}

function splitList(value: string): string[] {
  return value
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
}

export default function Items() {
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<ItemRead | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [importResult, setImportResult] = useState<ItemImportResult | null>(null)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  const [importConfirmed, setImportConfirmed] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const { data: items, isLoading, isError, error } = useQuery({
    queryKey: ['items'],
    queryFn: () => getItems(),
  })

  const createMut = useMutation({
    mutationFn: (p: ItemCreate) => createItem(p),
    onSettled: () => { qc.invalidateQueries({ queryKey: ['items'] }); setShowForm(false); setEditing(null) },
  })

  const updateMut = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: ItemUpdate }) => updateItem(id, patch),
    onSettled: () => { qc.invalidateQueries({ queryKey: ['items'] }); setShowForm(false); setEditing(null) },
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteItem(id),
    onSettled: () => qc.invalidateQueries({ queryKey: ['items'] }),
  })

  const importMut = useMutation({
    mutationFn: (file: File) => importItemsCsv(file, true),
    onSuccess: (result) => setImportResult(result),
    onError: (e) => setImportError((e as Error).message),
    onSettled: () => qc.invalidateQueries({ queryKey: ['items'] }),
  })

  const confirmImportMut = useMutation({
    mutationFn: (file: File) => importItemsCsv(file, false),
    onSuccess: (result) => setImportResult(result),
    onError: (e) => setImportError((e as Error).message),
    onSettled: () => qc.invalidateQueries({ queryKey: ['items'] }),
  })

  const handleExport = async (format: 'csv' | 'json') => {
    const blob = format === 'csv' ? await exportItemsCsv() : await exportItemsJson()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `items.${format}`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleTemplate = async (format: 'csv' | 'json') => {
    const blob = format === 'csv' ? await downloadTemplateCsv() : await downloadTemplateJson()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `items-template.${format}`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setImportFile(file)
    setImportError(null)
    setImportResult(null)
    setImportConfirmed(false)
    importMut.mutate(file)
  }

  const closeImport = () => {
    setImportResult(null)
    setImportFile(null)
    setImportError(null)
    setImportConfirmed(false)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const confirmImport = () => {
    if (!importFile) return
    setImportConfirmed(true)
    confirmImportMut.mutate(importFile)
  }

  return (
    <div className="p-4 sm:p-6 max-w-7xl mx-auto space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold text-gray-900">Items</h1>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            className="inline-flex items-center gap-1.5 px-3 py-2 bg-deal-fresh text-white rounded-lg text-sm font-medium hover:bg-green-700"
            onClick={() => { setEditing(null); setShowForm(true) }}
          >
            <Plus size={16} /> Add item
          </button>

          {/* Import */}
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv"
            className="hidden"
            onChange={handleFileSelect}
          />
          <button
            className="inline-flex items-center gap-1.5 px-3 py-2 bg-gray-700 text-white rounded-lg text-sm font-medium hover:bg-gray-800"
            onClick={() => fileInputRef.current?.click()}
          >
            <Upload size={16} /> Import CSV
          </button>

          {/* Export */}
          <button
            className="inline-flex items-center gap-1.5 px-3 py-2 bg-gray-100 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-200"
            onClick={() => handleExport('csv')}
          >
            <Download size={16} /> CSV
          </button>
          <button
            className="inline-flex items-center gap-1.5 px-3 py-2 bg-gray-100 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-200"
            onClick={() => handleExport('json')}
          >
            <Download size={16} /> JSON
          </button>

          {/* Templates */}
          <button
            className="inline-flex items-center gap-1.5 px-3 py-2 text-gray-500 rounded-lg text-sm font-medium hover:bg-gray-100"
            onClick={() => handleTemplate('csv')}
          >
            <FileDown size={16} /> Template
          </button>
        </div>
      </div>

      {/* Import result modal */}
      {importResult && (
        <ImportResultModal
          result={importResult}
          file={importFile}
          isPreview={!importConfirmed}
          confirming={confirmImportMut.isPending}
          onConfirm={confirmImport}
          onClose={closeImport}
        />
      )}
      {importError && (
        <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
          <AlertCircle size={16} />
          Import failed: {importError}
          <button onClick={closeImport} className="ml-auto text-red-400 hover:text-red-600"><X size={16} /></button>
        </div>
      )}
      {importMut.isPending && (
        <div className="flex items-center gap-2 text-sm text-gray-600 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
          <Upload size={16} className="animate-pulse" />
          Preparing import preview…
        </div>
      )}
      {confirmImportMut.isPending && (
        <div className="flex items-center gap-2 text-sm text-gray-600 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
          <Upload size={16} className="animate-pulse" />
          Importing…
        </div>
      )}

      {/* Add/edit form */}
      {showForm && (
        <ItemForm
          item={editing}
          onSubmit={(payload) => {
            if (editing) updateMut.mutate({ id: editing.id, patch: payload })
            else createMut.mutate(payload)
          }}
          onCancel={() => { setShowForm(false); setEditing(null) }}
          submitting={createMut.isPending || updateMut.isPending}
          error={(createMut.error as Error)?.message || (updateMut.error as Error)?.message}
        />
      )}

      {/* Items table */}
      {isLoading ? (
        <div className="animate-pulse space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-14 bg-gray-200 rounded-lg" />
          ))}
        </div>
      ) : isError ? (
        <ErrorBanner message={(error as Error).message} />
      ) : !items || items.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="w-8" />
                  <Th>Name</Th>
                  <Th>Category</Th>
                  <Th>Unit</Th>
                  <Th>Keywords</Th>
                  <Th>Status</Th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {items.map((item) => (
                  <ItemRow
                    key={item.id}
                    item={item}
                    expanded={expanded === item.id}
                    onToggle={() => setExpanded(expanded === item.id ? null : item.id)}
                    onEdit={() => { setEditing(item); setShowForm(true) }}
                    onDelete={() => {
                      if (confirm(`Delete "${item.name}"?`)) deleteMut.mutate(item.id)
                    }}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Item row with expandable history ───────────────────────────
function ItemRow({
  item, expanded, onToggle, onEdit, onDelete,
}: {
  item: ItemRead
  expanded: boolean
  onToggle: () => void
  onEdit: () => void
  onDelete: () => void
}) {
  return (
    <>
      <tr className="hover:bg-gray-50">
        <td className="px-2 py-3 text-center">
          <button onClick={onToggle} className="text-gray-400 hover:text-gray-600">
            {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          </button>
        </td>
        <Td><span className="font-medium text-gray-900">{item.name}</span></Td>
        <Td><span className="text-sm text-gray-600">{item.category || '—'}</span></Td>
        <Td>
          <span className="text-sm text-gray-500">
            {item.unit_of_measure || '—'}
            {item.typical_quantity && item.typical_quantity !== 1 ? ` × ${item.typical_quantity}` : ''}
          </span>
        </Td>
        <Td>
          <span className="text-sm text-gray-400 line-clamp-1 max-w-xs">
            {joinList(item.match_keywords) || '—'}
          </span>
        </Td>
        <Td>
          <span
            className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
              item.active
                ? 'bg-green-100 text-deal-fresh'
                : 'bg-gray-100 text-gray-500'
            }`}
          >
            {item.active ? 'Active' : 'Inactive'}
          </span>
        </Td>
        <td className="px-4 py-3 text-right whitespace-nowrap">
          <button onClick={onEdit} className="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded" title="Edit">
            <Pencil size={15} />
          </button>
          <button onClick={onDelete} className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded ml-1" title="Delete">
            <Trash2 size={15} />
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-gray-50/50">
          <td />
          <td colSpan={6} className="px-4 pb-4">
            <PriceHistoryChart itemId={item.id} />
          </td>
        </tr>
      )}
    </>
  )
}

// ── Price history chart ────────────────────────────────────────
function PriceHistoryChart({ itemId }: { itemId: number }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['item-history', itemId],
    queryFn: () => getItemHistory(itemId),
    enabled: !!itemId,
  })

  if (isLoading) return <div className="h-48 bg-gray-200 rounded-lg animate-pulse" />
  if (isError) return <p className="text-sm text-red-500">Failed to load price history.</p>
  if (!data || data.length === 0) return <p className="text-sm text-gray-400 py-4">No price history yet.</p>

  const chartData = data.map((d: PriceHistoryRead) => ({
    date: new Date(d.week).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
    price: d.best_unit_price / 100,
    deal: d.deal_type,
  }))

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-3">
      <p className="text-sm font-medium text-gray-600 mb-2">Price history (best unit price)</p>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={chartData} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="#9ca3af" />
          <YAxis
            tick={{ fontSize: 11 }} stroke="#9ca3af"
            tickFormatter={(v) => `$${v.toFixed(2)}`}
          />
          <Tooltip
            formatter={(v) => [`$${Number(v ?? 0).toFixed(2)}`, 'Unit price']}
            contentStyle={{ fontSize: 12, borderRadius: 8 }}
          />
          <Line
            type="monotone"
            dataKey="price"
            stroke="#15803d"
            strokeWidth={2}
            dot={{ r: 3, fill: '#15803d' }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Item form ──────────────────────────────────────────────────
function ItemForm({
  item, onSubmit, onCancel, submitting, error,
}: {
  item: ItemRead | null
  onSubmit: (p: ItemCreate) => void
  onCancel: () => void
  submitting: boolean
  error?: string
}) {
  const [name, setName] = useState(item?.name ?? '')
  const [category, setCategory] = useState(item?.category ?? '')
  const [matchKeywords, setMatchKeywords] = useState(joinList(item?.match_keywords))
  const [excludeKeywords, setExcludeKeywords] = useState(joinList(item?.exclude_keywords))
  const [preferredBrands, setPreferredBrands] = useState(joinList(item?.preferred_brands))
  const [unitOfMeasure, setUnitOfMeasure] = useState(item?.unit_of_measure ?? 'ea')
  const [typicalQuantity, setTypicalQuantity] = useState(String(item?.typical_quantity ?? 1))
  const [baselineOverride, setBaselineOverride] = useState(
    item?.baseline_price_override != null ? String(item.baseline_price_override / 100) : '',
  )
  const [active, setActive] = useState(item?.active ?? true)

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const baselineDollars = baselineOverride.trim() === '' ? null : Math.round(parseFloat(baselineOverride) * 100)
    const qty = parseFloat(typicalQuantity) || 1
    onSubmit({
      name,
      category,
      match_keywords: splitList(matchKeywords),
      exclude_keywords: splitList(excludeKeywords),
      preferred_brands: splitList(preferredBrands),
      unit_of_measure: unitOfMeasure || 'ea',
      typical_quantity: qty,
      baseline_price_override: baselineDollars,
      active,
    })
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">{item ? 'Edit item' : 'Add item'}</h2>
        <button onClick={onCancel} className="text-gray-400 hover:text-gray-600"><X size={18} /></button>
      </div>
      <form onSubmit={submit} className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Field label="Name *">
          <input
            required value={name} onChange={(e) => setName(e.target.value)}
            className="input"
          />
        </Field>
        <Field label="Category">
          <input
            value={category} onChange={(e) => setCategory(e.target.value)}
            className="input"
          />
        </Field>
        <Field label="Match keywords (comma-separated)">
          <input
            value={matchKeywords} onChange={(e) => setMatchKeywords(e.target.value)}
            className="input" placeholder="e.g. milk, whole, gallon"
          />
        </Field>
        <Field label="Exclude keywords (comma-separated)">
          <input
            value={excludeKeywords} onChange={(e) => setExcludeKeywords(e.target.value)}
            className="input" placeholder="e.g. organic, almond"
          />
        </Field>
        <Field label="Preferred brands (comma-separated)">
          <input
            value={preferredBrands} onChange={(e) => setPreferredBrands(e.target.value)}
            className="input" placeholder="e.g. Kroger, Great Value"
          />
        </Field>
        <Field label="Unit of measure">
          <input
            value={unitOfMeasure} onChange={(e) => setUnitOfMeasure(e.target.value)}
            className="input" placeholder="e.g. oz, lb, ea"
          />
        </Field>
        <Field label="Typical quantity">
          <input
            type="number" min={0.01} step={0.01}
            value={typicalQuantity} onChange={(e) => setTypicalQuantity(e.target.value)}
            className="input"
          />
        </Field>
        <Field label="Baseline price override ($)">
          <input
            type="number" min={0} step={0.01}
            value={baselineOverride} onChange={(e) => setBaselineOverride(e.target.value)}
            className="input" placeholder="Leave blank for auto"
          />
        </Field>
        <Field label="Active" full>
          <button
            type="button"
            role="switch"
            aria-checked={active}
            onClick={() => setActive(!active)}
            className={`relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors ${active ? 'bg-deal-fresh' : 'bg-gray-300'}`}
          >
            <span className={`inline-block h-5 w-5 rounded-full bg-white shadow transform transition-transform mt-0.5 ${active ? 'translate-x-5' : 'translate-x-0.5'}`} />
          </button>
        </Field>
        {error && (
          <div className="sm:col-span-2 text-sm text-red-600 flex items-center gap-1">
            <AlertCircle size={14} /> {error}
          </div>
        )}
        <div className="sm:col-span-2 flex justify-end gap-2">
          <button type="button" onClick={onCancel} className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg text-sm font-medium">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="px-4 py-2 bg-deal-fresh text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50"
          >
            {submitting ? 'Saving…' : item ? 'Save' : 'Add'}
          </button>
        </div>
      </form>
    </div>
  )
}

// ── Import result modal ───────────────────────────────────────
function ImportResultModal({
  result, file, isPreview, confirming, onConfirm, onClose,
}: {
  result: ItemImportResult
  file: File | null
  isPreview: boolean
  confirming: boolean
  onConfirm: () => void
  onClose: () => void
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 no-print">
      <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between p-4 border-b">
          <div>
            <h2 className="text-lg font-semibold">
              {isPreview ? 'Import preview' : 'Import result'}
            </h2>
            <p className="text-sm text-gray-500">
              {isPreview ? (
                <>
                  {result.total_rows} rows: {result.skipped_duplicates} duplicates, {result.errors.length} errors, {result.preview.length - result.skipped_duplicates} new items ready to import
                  {file && ` · ${file.name}`}
                </>
              ) : (
                <>
                  {result.imported} imported, {result.skipped_duplicates} duplicates skipped, {result.errors.length} errors out of {result.total_rows} rows
                  {file && ` · ${file.name}`}
                </>
              )}
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X size={18} /></button>
        </div>
        <div className="overflow-y-auto p-4 flex-1">
          {result.errors.length > 0 && (
            <div className="mb-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2">
              {result.errors.slice(0, 10).map((e, i) => (
                <div key={i}>{e}</div>
              ))}
              {result.errors.length > 10 && <div>…and {result.errors.length - 10} more</div>}
            </div>
          )}
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase text-gray-500">
              <tr>
                <th className="px-2 py-2 text-left">Name</th>
                <th className="px-2 py-2 text-left">Category</th>
                <th className="px-2 py-2 text-left">Unit</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {result.preview.map((r, i) => (
                <tr key={`${r.id}-${i}`}>
                  <td className="px-2 py-1.5">{r.name}</td>
                  <td className="px-2 py-1.5">{r.category || '—'}</td>
                  <td className="px-2 py-1.5">{r.unit_of_measure || '—'}</td>
                </tr>
              ))}
              {result.preview.length === 0 && (
                <tr>
                  <td colSpan={3} className="px-2 py-4 text-center text-gray-400">No items to import.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex justify-end gap-2 p-4 border-t">
          {isPreview ? (
            <>
              <button
                onClick={onClose}
                disabled={confirming}
                className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg text-sm font-medium disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={onConfirm}
                disabled={confirming || result.preview.filter(p => p.id === 0).length === 0}
                className="px-4 py-2 bg-deal-fresh text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50"
              >
                {confirming ? 'Importing…' : 'Confirm import'}
              </button>
            </>
          ) : (
            <button onClick={onClose} className="px-4 py-2 bg-deal-fresh text-white rounded-lg text-sm font-medium hover:bg-green-700">
              Done
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Shared bits ────────────────────────────────────────────────
function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider text-left">{children}</th>
}
function Td({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-4 py-3 ${className}`}>{children}</td>
}
function Field({ label, children, full }: { label: string; children: React.ReactNode; full?: boolean }) {
  return (
    <div className={full ? 'sm:col-span-2' : ''}>
      <label className="block text-xs font-medium text-gray-500 mb-1">{label}</label>
      {children}
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
function EmptyState() {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-10 text-center">
      <FileSpreadsheet className="mx-auto text-gray-300" size={40} />
      <p className="mt-3 text-gray-500">No items yet. Add your first item or import a list.</p>
    </div>
  )
}
