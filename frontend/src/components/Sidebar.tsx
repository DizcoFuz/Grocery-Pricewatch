import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  Package,
  Store as StoreIcon,
  ShoppingCart,
  Settings as SettingsIcon,
  ClipboardCheck,
  X,
} from 'lucide-react'

interface SidebarProps {
  open: boolean
  onClose: () => void
  reviewCount: number
}

const navItems = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/items', label: 'Items', icon: Package, end: false },
  { to: '/stores', label: 'Stores', icon: StoreIcon, end: false },
  { to: '/shopping-list', label: 'Shopping List', icon: ShoppingCart, end: false },
  { to: '/review-queue', label: 'Review Queue', icon: ClipboardCheck, end: false, badgeKey: 'review' },
  { to: '/settings', label: 'Settings', icon: SettingsIcon, end: false },
]

export default function Sidebar({ open, onClose, reviewCount }: SidebarProps) {
  return (
    <>
      {/* Mobile overlay */}
      {open && (
        <div
          className="fixed inset-0 z-30 bg-black/40 lg:hidden no-print"
          onClick={onClose}
          aria-hidden
        />
      )}

      <aside
        className={`
          no-print
          fixed z-40 inset-y-0 left-0 w-64 bg-white border-r border-gray-200
          flex flex-col transition-transform duration-200
          lg:static lg:translate-x-0 lg:z-auto
          ${open ? 'translate-x-0' : '-translate-x-full'}
        `}
      >
        {/* Brand header */}
        <div className="flex items-center justify-between h-16 px-4 border-b border-gray-200 bg-deal-fresh">
          <div className="flex items-center gap-2 text-white">
            <span className="text-xl">🛒</span>
            <span className="font-bold text-lg tracking-tight">Pricewatch</span>
          </div>
          <button
            className="lg:hidden text-white hover:bg-white/10 p-1 rounded"
            onClick={onClose}
            aria-label="Close menu"
          >
            <X size={20} />
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 px-2 py-4 space-y-1 overflow-y-auto">
          {navItems.map((item) => {
            const Icon = item.icon
            const showBadge =
              'badgeKey' in item && item.badgeKey === 'review' && reviewCount > 0
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={onClose}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors
                  ${isActive
                    ? 'bg-green-50 text-deal-fresh'
                    : 'text-gray-700 hover:bg-gray-100'}`
                }
              >
                <Icon size={18} className="shrink-0" />
                <span className="flex-1">{item.label}</span>
                {showBadge && (
                  <span className="inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1 text-xs font-semibold text-white bg-deal-bad rounded-full">
                    {reviewCount}
                  </span>
                )}
              </NavLink>
            )
          })}
        </nav>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-gray-200 text-xs text-gray-400">
          v1.0 · <a href="https://hermes-agent.nousresearch.com" className="hover:text-gray-600">Hermes</a>
        </div>
      </aside>
    </>
  )
}
