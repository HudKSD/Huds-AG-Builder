import { useMemo, useState } from 'react'

export function TraceTimeline({ steps }: { steps: any[] }) {
  const [q, setQ] = useState('')
  const filtered = useMemo(() => {
    if (!q.trim()) return steps
    const s = q.toLowerCase()
    return steps.filter(step => JSON.stringify(step).toLowerCase().includes(s))
  }, [q, steps])

  return (
    <div className="glass p-3">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm text-fuchsia-100">Trace Timeline</h3>
        <span className="text-xs text-cyan-200/70">{filtered.length} steps</span>
      </div>
      <input className="panel-input w-full h-9 mb-2" placeholder="Search trace…" value={q} onChange={e => setQ(e.target.value)} />
      <div className="space-y-2 max-h-72 overflow-auto">
        {filtered.map((s, i) => (
          <pre key={i} className="result-pre">{JSON.stringify(s, null, 2)}</pre>
        ))}
      </div>
    </div>
  )
}
