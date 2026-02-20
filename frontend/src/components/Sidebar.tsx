const items = ['Agents', 'Indices', 'Tools', 'Conversations', 'Admin']

export const Sidebar = () => (
  <aside className="p-4 glass min-h-screen">
    <p className="text-xs uppercase tracking-widest text-fuchsia-200/70 mb-3">Navigation</p>
    <nav className="space-y-2">
      {items.map(item => (
        <button key={item} className="w-full text-left px-3 py-2 rounded-md nav-item">
          {item}
        </button>
      ))}
    </nav>
  </aside>
)
