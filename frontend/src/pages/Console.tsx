import { useState } from 'react'
import { ChatPanel } from '../components/ChatPanel'
import { TraceTimeline } from '../components/TraceTimeline'
import { QueryInspector } from '../components/QueryInspector'

export function ConsolePage() {
  const [steps, setSteps] = useState<any[]>([])
  return <div className="grid grid-cols-3 gap-3"><div className="col-span-2"><ChatPanel onTrace={setSteps} /></div><div className="space-y-3"><TraceTimeline steps={steps}/><QueryInspector steps={steps}/></div></div>
}
