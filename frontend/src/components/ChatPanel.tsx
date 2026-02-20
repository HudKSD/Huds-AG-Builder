import { useState } from 'react'
import { postChat } from '../api/client'

export function ChatPanel({ onTrace }: { onTrace: (s: any[]) => void }) {
  const [msg, setMsg] = useState('')
  const [answer, setAnswer] = useState('')
  return <div className="p-4 rounded-xl glass"><textarea value={msg} onChange={e => setMsg(e.target.value)} className="w-full h-28 bg-slate-900"/><button className="btn" onClick={async()=>{const r=await postChat(msg);setAnswer(r.answer);onTrace(r.steps||[])}}>Run</button><pre>{answer}</pre></div>
}
