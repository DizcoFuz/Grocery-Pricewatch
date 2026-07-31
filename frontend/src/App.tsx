import { useState } from 'react'
import { Routes, Route } from 'react-router-dom'
import { Menu } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import Sidebar from './components/Sidebar'
import Dashboard from './pages/Dashboard'
import Items from './pages/Items'
import Stores from './pages/Stores'
import ShoppingList from './pages/ShoppingList'
import Settings from './pages/Settings'
import ReviewQueue from './pages/ReviewQueue'
import { getReviewQueue } from './api'

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  // Fetch review count for the sidebar badge
  const { data: reviewQueue } = useQuery({
    queryKey: ['review-queue'],
    queryFn: getReviewQueue,
    refetchInterval: 60_000,
  })
  const reviewCount = reviewQueue?.length ?? 0

  return (
    <div className="flex h-screen overflow-hidden bg-gray-50">
      <Sidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        reviewCount={reviewCount}
      />

      <div className="flex-1 flex flex-col min-w-0">
        {/* Mobile top bar */}
        <header className="lg:hidden flex items-center gap-3 h-14 px-3 bg-white border-b border-gray-200 no-print">
          <button
            className="p-1.5 rounded-md hover:bg-gray-100"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open menu"
          >
            <Menu size={22} />
          </button>
          <span className="font-bold text-gray-800">🛒 Pricewatch</span>
        </header>

        <main className="flex-1 overflow-y-auto">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/items" element={<Items />} />
            <Route path="/stores" element={<Stores />} />
            <Route path="/shopping-list" element={<ShoppingList />} />
            <Route path="/review-queue" element={<ReviewQueue />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}
