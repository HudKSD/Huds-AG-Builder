const timeline = document.getElementById('timeline');
const debug = document.getElementById('debug');
const queryDiff = document.getElementById('queryDiff');
const evidence = document.getElementById('evidence');
const blocked = document.getElementById('blocked');

function prefs() {
  return {
    selected_index: document.getElementById('selected_index').value || null,
    mode: document.getElementById('mode').value,
    time_range_hours: Number(document.getElementById('time_range_hours').value),
    max_rows: Number(document.getElementById('max_rows').value),
    baseline_first: document.getElementById('baseline_first').checked,
    baseline_window_hours: Number(document.getElementById('baseline_window_hours').value),
  };
}

function addEvent(e) {
  const li = document.createElement('li');
  li.textContent = `${e.type} ${e.node || e.tool || ''} ${e.duration_ms ? `(${e.duration_ms}ms)` : ''}`;
  timeline.appendChild(li);
  if (e.type === 'blocked') {
    blocked.classList.remove('hidden');
    blocked.innerHTML = `<strong>Query blocked</strong><br>${e.reason}<br>${(e.suggested_fixes || []).map((f, i) => `<button data-f='${JSON.stringify(f)}'>Fix ${i+1}</button>`).join(' ')}`;
    blocked.querySelectorAll('button').forEach(btn => {
      btn.onclick = () => {
        const f = JSON.parse(btn.dataset.f);
        if (f.action === 'set_time_range') document.getElementById('time_range_hours').value = f.value;
        if (f.action === 'set_max_rows') document.getElementById('max_rows').value = f.value;
      };
    });
  }
  if (e.type === 'final') {
    debug.textContent = JSON.stringify(e.debug, null, 2);
    queryDiff.textContent = `v1:\n${JSON.stringify(e.debug.query_v1, null, 2)}\n\nv2:\n${JSON.stringify(e.debug.query_v2, null, 2)}\n\nWhy: index=${e.debug.selected_index}, mode=${e.debug.chosen_mode}`;
    evidence.innerHTML = (e.evidence_cards || []).map(c => `<div><h4>${c.claim}</h4><pre>${JSON.stringify(c, null, 2)}</pre></div>`).join('');
  }
}

async function run(url) {
  timeline.innerHTML = '';
  blocked.classList.add('hidden');
  const res = await fetch(url, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ message: document.getElementById('message').value, preferences: prefs() })
  });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    buf += decoder.decode(value, {stream: true});
    let idx;
    while ((idx = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (line) addEvent(JSON.parse(line));
    }
  }
}

document.getElementById('run').onclick = () => run('/chat/stream');
document.getElementById('dryRun').onclick = () => run('/dry_run/stream');

document.getElementById('loadFields').onclick = async () => {
  const index = document.getElementById('selected_index').value;
  const r = await fetch(`/schema/fields?index=${encodeURIComponent(index)}`);
  const data = await r.json();
  const ul = document.getElementById('fields');
  ul.innerHTML = '';
  (data.fields || []).slice(0,80).forEach(f => {
    const li = document.createElement('li');
    li.textContent = `${f.name} (${f.type})`;
    li.onclick = () => document.getElementById('message').value += ` ${f.name}`;
    ul.appendChild(li);
  });
};

document.querySelectorAll('#playbooks button').forEach(btn => {
  btn.onclick = () => {
    const pb = btn.dataset.playbook;
    document.getElementById('message').value = pb.replace(/_/g, ' ');
  }
});
