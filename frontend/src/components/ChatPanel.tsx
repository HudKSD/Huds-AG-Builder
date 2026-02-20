import { useMemo, useState } from 'react'
import { postChat } from '../api/client'

const PROMPTS = [
  'Top source.ip with failed auth in last 24h',
  'Show schema for detections*',
  'Count alerts by severity',
]

export function ChatPanel({ onTrace }: { onTrace: (s: any[]) => void }) {
  const [msg, setMsg] = useState('')
  const [answer, setAnswer] = useState('')
  const [loading, setLoading] = useState(false)
  const canRun = useMemo(() => msg.trim().length > 0 && !loading, [msg, loading])

  return (
    <div className="p-4 rounded-xl glass space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm uppercase tracking-widest text-fuchsia-200/80">Chat</h2>
        {loading ? <span className="status-chip">Tool running…</span> : <span className="status-chip idle">Ready</span>}
      </div>

      <div className="flex flex-wrap gap-2">
        {PROMPTS.map(p => (
          <button key={p} className="chip" onClick={() => setMsg(p)}>{p}</button>
        ))}
      </div>

      <textarea
        value={msg}
        onChange={e => setMsg(e.target.value)}
        className="w-full h-32 panel-input"
        placeholder="Ask about your Elasticsearch data…"
      />

      <button
        className="btn"
        disabled={!canRun}
        onClick={async () => {
          setLoading(true)
          try {
            const r = await postChat(msg)
            setAnswer(r.answer || '')
            onTrace(r.steps || [])
          } finally {
            setLoading(false)
          }
        }}
      >
        {loading ? 'Running…' : 'Run Query'}
      </button>

      <div>
        <h3 className="text-xs uppercase tracking-widest text-cyan-200/70 mb-2">Assistant Output</h3>
        <pre className="result-pre">{answer || 'No response yet.'}</pre>
      </div>
    </div>
  )
}
