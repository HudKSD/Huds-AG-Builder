import { TopBar } from '../components/TopBar'
import { Sidebar } from '../components/Sidebar'
import { ConsolePage } from '../pages/Console'

export function App() {
  return (
    <div className="text-slate-100 min-h-screen bg-grid">
      <TopBar />
      <div className="grid grid-cols-[240px_1fr] gap-4 p-4">
        <Sidebar />
        <ConsolePage />
      </div>
    </div>
  )
}
