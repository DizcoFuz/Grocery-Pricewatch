// ============================================================
// Grocery Pricewatch — TypeScript types matching backend schemas
// All prices are integer cents (display as $X.XX).
// Field names are snake_case to match the Pydantic JSON output.
// ============================================================

// ── Store ────────────────────────────────────────────────────

/** A store/retailer we scrape ads from. */
export interface StoreRead {
  id: number
  name: string
  adapter_key: string
  zip_or_store_id: string
  enabled: boolean
  last_fetch_at: string | null   // ISO timestamp
  last_fetch_status: string | null
}

/** Payload for creating a store. */
export interface StoreCreate {
  name: string
  adapter_key: string
  zip_or_store_id?: string
  enabled?: boolean
}

/** Payload for updating a store. */
export interface StoreUpdate {
  name?: string | null
  adapter_key?: string | null
  zip_or_store_id?: string | null
  enabled?: boolean | null
}

// ── Item ─────────────────────────────────────────────────────

/** A grocery item the user tracks. */
export interface ItemRead {
  id: number
  name: string
  category: string
  match_keywords: string[]
  exclude_keywords: string[]
  preferred_brands: string[]
  unit_of_measure: string
  typical_quantity: number
  baseline_price_override: number | null  // cents
  active: boolean
  created_at: string
}

/** Payload for creating an item. */
export interface ItemCreate {
  name: string
  category: string
  match_keywords: string[]
  exclude_keywords: string[]
  preferred_brands: string[]
  unit_of_measure: string
  typical_quantity: number
  baseline_price_override: number | null
  active: boolean
}

/** Payload for updating an item. */
export interface ItemUpdate {
  name?: string | null
  category?: string | null
  match_keywords?: string[] | null
  exclude_keywords?: string[] | null
  preferred_brands?: string[] | null
  unit_of_measure?: string | null
  typical_quantity?: number | null
  baseline_price_override?: number | null
  active?: boolean | null
}

/** One row from a CSV/JSON item import. */
export interface ItemImportRow {
  name: string
  category?: string
  match_keywords?: string[]
  exclude_keywords?: string[]
  preferred_brands?: string[]
  unit_of_measure?: string
  typical_quantity?: number
  baseline_price_override?: number | null
  active?: boolean
}

/** Summary of an import operation. */
export interface ItemImportResult {
  total_rows: number
  imported: number
  skipped_duplicates: number
  errors: string[]
  preview: ItemRead[]
}

// ── Offer ────────────────────────────────────────────────────

/** A raw offer scraped from a store ad. */
export interface OfferRead {
  id: number
  ad_cycle_id: number
  raw_text: string
  product_name: string
  brand: string
  size_text: string
  price: number                    // cents
  deal_type: string
  effective_unit_price: number     // cents
  unit_price_unknown: boolean
  requires_membership_or_coupon: boolean
}

// ── Match ────────────────────────────────────────────────────

export type MatchStatus = 'confident' | 'uncertain' | 'accepted' | 'rejected'
export type MatchDecidedBy = 'auto' | 'user'

/** A match linking an offer to a tracked item. */
export interface MatchRead {
  id: number
  offer_id: number
  item_id: number
  confidence: number              // 0..1
  status: MatchStatus
  decided_by: MatchDecidedBy
}

/** Match enriched with offer + item info for the review queue. */
export interface MatchWithDetails extends MatchRead {
  offer: OfferRead
  item_name: string
  store_name: string
}

/** User decision on an uncertain match. */
export interface MatchReview {
  decision: 'accept' | 'reject'
}

// ── Price history ────────────────────────────────────────────

export interface PriceHistoryRead {
  item_id: number
  store_id: number
  week: string                     // ISO date
  best_unit_price: number         // cents
  deal_type: string
}

// ── Weekly report ────────────────────────────────────────────

export interface WeeklyReportRead {
  week: string                     // ISO date
  best_single_store_id: number | null
  best_pair_store_ids: number[]
  projected_savings_single: number // cents
  projected_savings_pair: number    // cents
  per_item_results_json: string
}

// ── Settings ─────────────────────────────────────────────────

/** All settings in one response. */
export interface SettingsBundle {
  two_store_threshold: number      // cents (default 500)
  baseline_strategy: string        // default "auto"
  refresh_schedule: string         // default "07:00"
}

// ── Recommendations ──────────────────────────────────────────

/** Per-item cost within a store recommendation. */
export interface StoreCostDetail {
  item_id: number
  item_name: string
  unit_price: number               // cents
  quantity: number
  line_total: number                // cents
  is_sale: boolean
  deal_type: string
}

export interface SingleStoreRecommendation {
  store_id: number
  store_name: string
  total_cost: number               // cents
  baseline_cost: number            // cents
  savings: number                  // cents
  item_count: number
  details: StoreCostDetail[]
}

export interface TwoStoreRecommendation {
  store_ids: number[]
  store_names: string[]
  total_cost: number               // cents
  baseline_cost: number            // cents
  savings: number                  // cents
  marginal_benefit: number         // cents
  item_count: number
  details: StoreCostDetail[]
  item_store_map: Record<number, number>
}

export interface RecommendationsResponse {
  single: SingleStoreRecommendation[]
  best_single: SingleStoreRecommendation | null
  two_store: TwoStoreRecommendation[]
  best_pair: TwoStoreRecommendation | null
  two_store_threshold: number
}

// ── Savings ──────────────────────────────────────────────────

export interface SavingsResponse {
  weekly_savings: number           // cents
  cumulative_savings: number      // cents
  weekly_report: WeeklyReportRead | null
  history: WeeklyReportRead[]
}

// ── Dashboard ────────────────────────────────────────────────

export interface StoreStatus {
  store_id: number
  name: string
  enabled: boolean
  last_fetch_at: string | null    // ISO timestamp
  last_fetch_status: string | null
}

export interface DashboardBestDeal {
  item_name: string
  store_name: string
  sale_price: number               // cents
  unit_price: number               // cents
  deal_type: string
  savings_vs_baseline: number      // cents
}

export interface DashboardResponse {
  headline_savings: number         // cents
  headline_store: string
  headline_mode: 'single' | 'pair'
  best_deals: DashboardBestDeal[]
  store_statuses: StoreStatus[]
  review_queue_count: number
  last_report: WeeklyReportRead | null
  banner: string | null
  best_prices: BestPricesResponse | null
}

// ── Best Prices (FR-4.2, FR-4.3) ──────────────────────────────

export interface BestPriceEntry {
  item_id: number
  item_name: string
  category: string
  current_best_price: number | null   // cents
  current_best_store_id: number | null
  current_best_store_name: string
  current_best_deal_type: string
  last_best_price: number | null      // cents
  last_best_store_name: string
  last_best_week: string               // ISO date
  delta_cents: number | null          // negative = cheaper (better)
  delta_direction: '' | 'better' | 'worse' | 'unchanged' | 'new'
  all_time_best_price: number | null  // cents
  all_time_best_store_name: string
  all_time_best_week: string
  other_store_prices: Array<{
    store_name: string
    price: number
    deal_type: string
    unit_price_unknown: boolean
  }>
  unit_price_unknown: boolean
}

export interface BestPricesResponse {
  items_with_deals: BestPriceEntry[]
  items_without_deals: BestPriceEntry[]
}

// ── Shopping list ────────────────────────────────────────────

export interface ShoppingListEntry {
  item_id: number
  item_name: string
  quantity: number
  unit_of_measure: string
  store_id: number
  store_name: string
  unit_price: number               // cents
  line_total: number               // cents
  is_sale: boolean
  deal_type: string
}

export interface ShoppingListResponse {
  mode: 'single' | 'pair'
  total_cost: number               // cents
  baseline_cost: number            // cents
  savings: number                  // cents
  store_ids: number[]
  store_names: string[]
  entries: ShoppingListEntry[]
}

// ── Refresh ─────────────────────────────────────────────────

export interface StoreRefreshResult {
  store_id: number
  store_name: string
  status: string
  offers_fetched: number
  matches_created: number
  error: string | null
}

export interface RefreshAllResult {
  results: StoreRefreshResult[]
  total_offers: number
  total_matches: number
  weekly_report: WeeklyReportRead | null
}

// ============================================================
// Helpers
// ============================================================

/** Format integer cents as a USD string, e.g. 549 -> "$5.49". */
export function formatCents(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return '—'
  const sign = cents < 0 ? '-' : ''
  const abs = Math.abs(cents)
  return `${sign}$${(abs / 100).toFixed(2)}`
}

/** Format a delta in cents with a + or – sign, e.g. -50 -> "−$0.50". */
export function formatDeltaCents(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return '—'
  const sign = cents > 0 ? '+' : cents < 0 ? '−' : ''
  return `${sign}$${(Math.abs(cents) / 100).toFixed(2)}`
}

/** Human-readable relative time, e.g. "3 min ago". */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return 'never'
  const then = new Date(iso).getTime()
  const now = Date.now()
  const diff = Math.max(0, now - then)
  const s = Math.floor(diff / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  return `${d}d ago`
}
