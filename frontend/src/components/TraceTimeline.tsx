export function TraceTimeline({ steps }: { steps: any[] }) {
  return <div className="glass p-3"><h3>Trace</h3>{steps.map((s,i)=><pre key={i}>{JSON.stringify(s,null,2)}</pre>)}</div>
}
