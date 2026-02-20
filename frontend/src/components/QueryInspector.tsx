export const QueryInspector = ({ steps }: { steps: any[] }) => {
  const calls = steps.filter(s => s.type === 'tool_call')
  return (
    <div className="glass p-3">
      <h3 className="text-sm text-fuchsia-100 mb-2">Query Inspector</h3>
      <pre className="result-pre">{JSON.stringify(calls, null, 2) || 'No tool calls yet.'}</pre>
    </div>
  )
}
