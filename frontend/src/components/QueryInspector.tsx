export const QueryInspector = ({ steps }: { steps: any[] }) => <div className="glass p-3"><h3>Query Inspector</h3><pre>{JSON.stringify(steps.filter(s=>s.type==='tool_call'),null,2)}</pre></div>
