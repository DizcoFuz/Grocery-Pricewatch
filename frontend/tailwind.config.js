/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        deal: {
          best: '#16a34a',      // green-600 — best price / savings
          good: '#65a30d',       // lime-600 — good deal
          ok: '#ca8a04',         // yellow-600 — normal
          bad: '#dc2626',        // red-600 — worse price
          new: '#7c3aed',        // violet-600 — first-ever price
          stale: '#a16207',      // yellow-700 — stale fetch
          partial: '#ea580c',    // orange-600 — partial fetch
          failed: '#b91c1c',     // red-700 — failed fetch
          fresh: '#15803d',      // green-700 — fresh fetch
        },
      },
    },
  },
  plugins: [],
}
