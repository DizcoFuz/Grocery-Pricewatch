import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Save,
  AlertCircle,
  Check,
  Settings as SettingsIcon,
} from 'lucide-react'
import { getSettings, updateSettings } from '../api'
import type { SettingsBundle } from '../types'
import { formatCents } from '../types'

export default function Settings() {
  const qc = useQueryClient()
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  })

  const updateMut = useMutation({
    mutationFn: (p: SettingsBundle) => updateSettings(p),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })

  if (isLoading) return <Loading />
  if (isError) return <ErrorBanner message={(error as Error).message} />
  if (!data) return null

  return (
    <div className="p-4 sm:p-6 max-w-2xl mx-auto space-y-6">
      <div className="flex items-center gap-2">
        <SettingsIcon className="text-gray-400" size={24} />
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
      </div>

      {updateMut.isSuccess && (
        <div className="flex items-center gap-2 text-sm text-deal-fresh bg-green-50 border border-green-200 rounded-lg px-3 py-2">
          <Check size={16} /> Settings saved.
        </div>
      )}

      <SettingsForm data={data} onSave={(p) => updateMut.mutate(p)} saving={updateMut.isPending} error={(updateMut.error as Error)?.message} />
    </div>
  )
}

function SettingsForm({
  data, onSave, saving, error,
}: {
  data: SettingsBundle
  onSave: (p: SettingsBundle) => void
  saving: boolean
  error?: string
}) {
  // two_store_threshold is in cents; display as dollars.
  const [threshold, setThreshold] = useState(data.two_store_threshold / 100)
  const [strategy, setStrategy] = useState(data.baseline_strategy)
  const [refreshSchedule, setRefreshSchedule] = useState(data.refresh_schedule)

  useEffect(() => {
    setThreshold(data.two_store_threshold / 100)
    setStrategy(data.baseline_strategy)
    setRefreshSchedule(data.refresh_schedule)
  }, [data])

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const payload: SettingsBundle = {
      two_store_threshold: Math.round(threshold * 100),
      baseline_strategy: strategy,
      refresh_schedule: refreshSchedule,
    }
    onSave(payload)
  }

  return (
    <form onSubmit={submit} className="bg-white rounded-xl border border-gray-200 p-5 space-y-6">
      {/* Two-store threshold */}
      <section>
        <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide mb-2">
          Two-store threshold
        </h2>
        <p className="text-xs text-gray-400 mb-3">
          Switch to a two-store trip when projected savings exceed this amount.
        </p>
        <div className="flex items-center gap-3">
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">$</span>
            <input
              type="number"
              min={0}
              step={0.5}
              value={threshold}
              onChange={(e) => setThreshold(parseFloat(e.target.value) || 0)}
              className="input pl-7 w-32"
            />
          </div>
          <span className="text-sm text-gray-400">
            = {formatCents(Math.round(threshold * 100))}
          </span>
        </div>
      </section>

      {/* Baseline strategy */}
      <section>
        <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide mb-2">
          Baseline price strategy
        </h2>
        <p className="text-xs text-gray-400 mb-3">
          How the "normal" reference price is determined for savings calculations.
        </p>
        <select
          className="input"
          value={strategy}
          onChange={(e) => setStrategy(e.target.value)}
        >
          <option value="auto">Auto</option>
          <option value="median">Median of recent prices</option>
          <option value="last30">Average of last 30 days</option>
          <option value="last_best">Last best price</option>
          <option value="manual">Manual (per item)</option>
        </select>
      </section>

      {/* Refresh schedule */}
      <section>
        <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide mb-2">
          Refresh schedule
        </h2>
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Daily refresh time</label>
            <input
              type="time"
              className="input w-32"
              value={refreshSchedule}
              onChange={(e) => setRefreshSchedule(e.target.value)}
            />
          </div>
        </div>
      </section>

      {/* Error */}
      {error && (
        <div className="text-sm text-red-600 flex items-center gap-1">
          <AlertCircle size={14} /> {error}
        </div>
      )}

      {/* Save */}
      <div className="flex justify-end pt-2 border-t border-gray-100">
        <button
          type="submit"
          disabled={saving}
          className="inline-flex items-center gap-1.5 px-4 py-2 bg-deal-fresh text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50"
        >
          <Save size={16} />
          {saving ? 'Saving…' : 'Save settings'}
        </button>
      </div>
    </form>
  )
}

function Loading() {
  return (
    <div className="p-6 max-w-2xl mx-auto">
      <div className="animate-pulse space-y-4">
        <div className="h-8 w-40 bg-gray-200 rounded" />
        <div className="h-64 bg-gray-200 rounded-xl" />
      </div>
    </div>
  )
}
function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="p-6 max-w-2xl mx-auto">
      <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-600 flex items-center gap-2">
        <AlertCircle size={16} /> {message}
      </div>
    </div>
  )
}
