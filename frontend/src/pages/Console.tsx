import { useMemo, useState } from 'react'
import { ChatPanel } from '../components/ChatPanel'
import { TraceTimeline } from '../components/TraceTimeline'
import { QueryInspector } from '../components/QueryInspector'

export function ConsolePage() {
  const [steps, setSteps] = useState<any[]>([])
  const metrics = useMemo(() => {
    const toolCalls = steps.filter(s => s.type === 'tool_call').length
    const results = steps.filter(s => s.type === 'tool_result').length
    return { toolCalls, results, steps: steps.length }
  }, [steps])

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-3">
        <div className="metric-card"><p>Steps</p><strong>{metrics.steps}</strong></div>
        <div className="metric-card"><p>Tool Calls</p><strong>{metrics.toolCalls}</strong></div>
        <div className="metric-card"><p>Results</p><strong>{metrics.results}</strong></div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div className="col-span-2"><ChatPanel onTrace={setSteps} /></div>
        <div className="space-y-3"><TraceTimeline steps={steps}/><QueryInspector steps={steps}/></div>
      </div>
    </div>
  )
}
