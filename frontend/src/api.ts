import axios from 'axios'
import type {
  DashboardResponse,
  BestPricesResponse,
  ItemRead,
  ItemCreate,
  ItemUpdate,
  ItemImportRow,
  ItemImportResult,
  StoreRead,
  StoreCreate,
  StoreUpdate,
  StoreRefreshResult,
  RefreshAllResult,
  PriceHistoryRead,
  MatchWithDetails,
  RecommendationsResponse,
  ShoppingListResponse,
  SavingsResponse,
  SettingsBundle,
} from './types'

// baseURL is empty so we can hit the SPA at "/" and all API routes at
// "/api/...". The backend serves the dashboard from GET /api/dashboard and
// the SPA from GET / (StaticFiles).
const client = axios.create({
  baseURL: '',
  headers: { 'Content-Type': 'application/json' },
  timeout: 60000,
})

// ── Dashboard ────────────────────────────────────────────────
export async function getDashboard(): Promise<DashboardResponse> {
  const { data } = await client.get<DashboardResponse>('/api/dashboard')
  return data
}

export async function getBestPrices(): Promise<BestPricesResponse> {
  const { data } = await client.get<BestPricesResponse>('/api/best-prices')
  return data
}

// ── Refresh-all (async: POST starts the job, GET polls status) ──
export interface RefreshAllStatus {
  running: boolean
  result: RefreshAllResult | { error: string } | null
  started_at: string | null
  finished_at: string | null
}

export async function refreshAll(): Promise<{ status: string; started_at?: string }> {
  const { data } = await client.post<{ status: string; started_at?: string }>('/api/refresh-all')
  return data
}

export async function getRefreshAllStatus(): Promise<RefreshAllStatus> {
  const { data } = await client.get<RefreshAllStatus>('/api/refresh-all/status')
  return data
}

export async function refreshStore(id: number): Promise<StoreRefreshResult> {
  const { data } = await client.post<StoreRefreshResult>(`/api/stores/${id}/refresh`)
  return data
}

// ── Items ─────────────────────────────────────────────────────
export async function getItems(active?: boolean): Promise<ItemRead[]> {
  const { data } = await client.get<ItemRead[]>('/api/items', {
    params: active === undefined ? {} : { active },
  })
  return data
}

export async function createItem(payload: ItemCreate): Promise<ItemRead> {
  const { data } = await client.post<ItemRead>('/api/items', payload)
  return data
}

export async function updateItem(id: number, payload: ItemUpdate): Promise<ItemRead> {
  const { data } = await client.put<ItemRead>(`/api/items/${id}`, payload)
  return data
}

export async function deleteItem(id: number): Promise<void> {
  await client.delete(`/api/items/${id}`)
}

// ── Items import / export / template ──────────────────────────
export async function importItemsCsv(file: File): Promise<ItemImportResult> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await client.post<ItemImportResult>('/api/items/import/csv', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export async function importItemsJson(rows: ItemImportRow[]): Promise<ItemImportResult> {
  const { data } = await client.post<ItemImportResult>('/api/items/import/json', rows)
  return data
}

export async function exportItemsCsv(): Promise<Blob> {
  const { data } = await client.get('/api/items/export/csv', { responseType: 'blob' })
  return data
}

export async function exportItemsJson(): Promise<Blob> {
  const { data } = await client.get('/api/items/export/json', { responseType: 'blob' })
  return data
}

export async function downloadTemplateCsv(): Promise<Blob> {
  const { data } = await client.get('/api/items/template/csv', { responseType: 'blob' })
  return data
}

export async function downloadTemplateJson(): Promise<Blob> {
  const { data } = await client.get('/api/items/template/json', { responseType: 'blob' })
  return data
}

export async function getItemHistory(id: number): Promise<PriceHistoryRead[]> {
  const { data } = await client.get<PriceHistoryRead[]>(`/api/items/${id}/history`)
  return data
}

// ── Stores ────────────────────────────────────────────────────
export async function getStores(): Promise<StoreRead[]> {
  const { data } = await client.get<StoreRead[]>('/api/stores')
  return data
}

export async function createStore(payload: StoreCreate): Promise<StoreRead> {
  const { data } = await client.post<StoreRead>('/api/stores', payload)
  return data
}

export async function updateStore(id: number, payload: StoreUpdate): Promise<StoreRead> {
  const { data } = await client.put<StoreRead>(`/api/stores/${id}`, payload)
  return data
}

export async function deleteStore(id: number): Promise<void> {
  await client.delete(`/api/stores/${id}`)
}

// ── Review Queue / Matches ─────────────────────────────────────
export async function getReviewQueue(): Promise<MatchWithDetails[]> {
  const { data } = await client.get<MatchWithDetails[]>('/api/matches/review')
  return data
}

export async function decideMatch(id: number, decision: 'accept' | 'reject'): Promise<MatchWithDetails> {
  const { data } = await client.post<MatchWithDetails>(`/api/matches/${id}/decide`, { decision })
  return data
}

// ── Recommendations / Shopping / Savings ────────────────────────
export async function getRecommendations(): Promise<RecommendationsResponse> {
  const { data } = await client.get<RecommendationsResponse>('/api/recommendations')
  return data
}

export async function getShoppingList(mode: 'single' | 'pair'): Promise<ShoppingListResponse> {
  const { data } = await client.get<ShoppingListResponse>('/api/shopping-list', { params: { mode } })
  return data
}

export async function getSavings(): Promise<SavingsResponse> {
  const { data } = await client.get<SavingsResponse>('/api/savings')
  return data
}

// ── Settings ──────────────────────────────────────────────────
export async function getSettings(): Promise<SettingsBundle> {
  const { data } = await client.get<SettingsBundle>('/api/settings')
  return data
}

export async function updateSettings(payload: SettingsBundle): Promise<SettingsBundle> {
  const { data } = await client.put<SettingsBundle>('/api/settings', payload)
  return data
}

export default client
