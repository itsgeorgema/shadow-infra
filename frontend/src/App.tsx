import { Route, Routes } from 'react-router-dom'
import PRList from './components/PRList'
import DriftReport from './components/DriftReport'

export default function App() {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="border-b border-gray-800 px-6 py-4">
        <div className="mx-auto max-w-7xl flex items-center gap-3">
          <span className="text-2xl font-bold tracking-tight text-white">Shadow-Infra</span>
          <span className="rounded-full bg-indigo-600 px-2 py-0.5 text-xs font-medium text-white">
            Drift Report
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">
        <Routes>
          <Route path="/" element={<PRList />} />
          <Route path="/deployment/:id" element={<DriftReport />} />
        </Routes>
      </main>
    </div>
  )
}
