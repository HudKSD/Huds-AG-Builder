import { useEffect, useState } from 'react'

export const TopBar = () => {
  const [now, setNow] = useState(new Date())

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  return (
    <header className="topbar px-4 py-3 flex items-center justify-between border-b border-fuchsia-400/20">
      <div>
        <p className="text-xs uppercase tracking-[0.2em] text-fuchsia-200/75">SOC Console</p>
        <h1 className="text-lg font-semibold text-white">Elastic Agent Builder</h1>
      </div>
      <div className="text-right">
        <p className="text-xs text-cyan-200/80">Environment: Production Preview</p>
        <p className="font-mono text-sm text-fuchsia-100">{now.toISOString().replace('T', ' ').slice(0, 19)}Z</p>
      </div>
    </header>
  )
}
