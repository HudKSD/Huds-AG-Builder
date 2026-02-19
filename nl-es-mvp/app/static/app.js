let conversationId = localStorage.getItem("conversation_id") || null;

const el = (id) => document.getElementById(id);

const messagesEl = el("messages");
const inputEl = el("input");
const sendBtn = el("send");
const sendLabelEl = el("sendLabel");
const spinnerEl = el("spinner");

const convIdEl = el("convId");
const btnCopyConv = el("btnCopyConv");
const btnResetConv = el("btnResetConv");
const btnClearUI = el("btnClearUI");

const esLabel = el("esLabel");
const dotEs = el("dotEs");

const ovStage = el("ovStage");
const ovIndex = el("ovIndex");
const ovCount = el("ovCount");
const ovTimeField = el("ovTimeField");

const hitsWrap = el("hitsWrap");
const hitsFilter = el("hitsFilter");

const queryUsedEl = el("queryUsed");
const traceBox = el("traceBox");
const rawBox = el("rawBox");

const btnCopyQuery = el("btnCopyQuery");
const btnPrettyQuery = el("btnPrettyQuery");
const btnCopyLastQuery = el("btnCopyLastQuery");
const btnCopyLastRaw = el("btnCopyLastRaw");
const btnExportHits = el("btnExportHits");

const modal = el("modal");
const modalBackdrop = el("modalBackdrop");
const modalTitle = el("modalTitle");
const modalBody = el("modalBody");
const btnCloseModal = el("btnCloseModal");
const btnCopyModal = el("btnCopyModal");

const toast = el("toast");
const reqMeta = el("reqMeta");

let lastResponse = null;
let lastQueryObj = null;
let lastHits = [];
let lastModalJSON = "";

/* -------------------------
   Utilities
------------------------- */
function nowTime() {
  const d = new Date();
  return d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function showToast(msg, ms=2600) {
  toast.textContent = msg;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), ms);
}

function setConversationId(id) {
  conversationId = id;
  if (conversationId) localStorage.setItem("conversation_id", conversationId);
  convIdEl.textContent = conversationId || "—";
}

function setLoading(on) {
  sendBtn.disabled = !!on;
  spinnerEl.classList.toggle("hidden", !on);
  sendLabelEl.textContent = on ? "Running…" : "Send";
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    showToast("Copied.");
  } catch (e) {
    showToast("Copy failed (clipboard not permitted).");
  }
}

/* -------------------------
   Safe Markdown renderer (ChatGPT-like)
   Supports:
   - headings (#, ##, ###)
   - bullet/number lists
   - blockquote (>)
   - bold ** **, italic * *
   - inline code ``
   - code blocks ```lang
   - links [text](https://...)
------------------------- */
function renderMarkdown(md) {
  if (!md) return "";

  // Normalize
  md = String(md).replace(/\r\n/g, "\n");

  // Escape HTML first
  let text = escapeHtml(md);

  // Extract fenced code blocks into tokens
  const blocks = [];
  text = text.replace(/```([\w-]+)?\n([\s\S]*?)```/g, (_, lang, code) => {
    const id = blocks.length;
    blocks.push({ lang: (lang || "").trim(), code });
    return `@@CODEBLOCK_${id}@@`;
  });

  // Inline code
  text = text.replace(/`([^`\n]+)`/g, `<code class="inline">$1</code>`);

  // Bold
  text = text.replace(/\*\*([^\*\n]+)\*\*/g, `<strong>$1</strong>`);

  // Italic (simple, avoids breaking on **)
  text = text.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g, `$1<em>$2</em>`);

  // Links
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    `<a href="$2" target="_blank" rel="noopener">$1</a>`
  );

  const lines = text.split("\n");
  let html = "";
  let i = 0;

  function isBlank(line) { return !line || !line.trim(); }
  function isUL(line) { return /^(\s*[-*•])\s+/.test(line); }
  function isOL(line) { return /^\s*\d+\.\s+/.test(line); }
  function isQuote(line) { return /^\s*>\s+/.test(line); }
  function isH(line) { return /^\s*#{1,3}\s+/.test(line); }

  function takeWhile(pred) {
    const out = [];
    while (i < lines.length && pred(lines[i])) {
      out.push(lines[i]);
      i++;
    }
    return out;
  }

  while (i < lines.length) {
    const line = lines[i];

    if (isBlank(line)) { i++; continue; }

    // Headings
    if (isH(line)) {
      const m = line.match(/^\s*(#{1,3})\s+(.*)$/);
      const lvl = m[1].length;
      const content = m[2].trim();
      html += `<h${lvl}>${content}</h${lvl}>`;
      i++;
      continue;
    }

    // Blockquote (one or more lines)
    if (isQuote(line)) {
      const qLines = takeWhile(isQuote).map(l => l.replace(/^\s*>\s+/, ""));
      html += `<blockquote>${qLines.join("<br>")}</blockquote>`;
      continue;
    }

    // Unordered list
    if (isUL(line)) {
      const items = takeWhile(isUL).map(l => l.replace(/^(\s*[-*•])\s+/, ""));
      html += `<ul>${items.map(it => `<li>${it}</li>`).join("")}</ul>`;
      continue;
    }

    // Ordered list
    if (isOL(line)) {
      const items = takeWhile(isOL).map(l => l.replace(/^\s*\d+\.\s+/, ""));
      html += `<ol>${items.map(it => `<li>${it}</li>`).join("")}</ol>`;
      continue;
    }

    // Paragraph: consume until blank or structure
    const para = takeWhile(l => !isBlank(l) && !isUL(l) && !isOL(l) && !isQuote(l) && !isH(l));
    html += `<p>${para.join("<br>")}</p>`;
  }

  // Restore code blocks
  for (let b = 0; b < blocks.length; b++) {
    const token = `@@CODEBLOCK_${b}@@`;
    const lang = blocks[b].lang || "text";
    const code = blocks[b].code; // already escaped (because we escaped first)

    const codeHtml = `
      <div class="codeblock">
        <div class="codeblock-head">
          <span class="lang">${escapeHtml(lang)}</span>
          <button type="button" class="copy-code-btn" data-copy-code="1">Copy</button>
        </div>
        <pre><code class="language-${escapeHtml(lang)}">${code}</code></pre>
      </div>
    `.trim();

    html = html.replaceAll(token, codeHtml);
  }

  return html;
}

/* -------------------------
   Chat rendering
------------------------- */
function addMessage(role, content) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = `
    <div class="meta">
      <div class="role">${role.toUpperCase()}</div>
      <div class="time">${nowTime()}</div>
    </div>
    <div class="content"></div>
  `;

  const contentEl = div.querySelector(".content");

  if (role === "assistant") {
    contentEl.classList.add("md");
    contentEl.innerHTML = renderMarkdown(content);
  } else {
    contentEl.textContent = content;
  }

  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;

  // wire copy buttons for any code blocks inside this message
  div.querySelectorAll(".copy-code-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const codeEl = btn.closest(".codeblock")?.querySelector("code");
      if (!codeEl) return;
      copyText(codeEl.textContent || "");
    });
  });
}

/* -------------------------
   Panels
------------------------- */
function pretty(obj) {
  return JSON.stringify(obj, null, 2);
}

function updateOverview(artifact) {
  ovStage.textContent = artifact?.stage ?? "—";
  ovIndex.textContent = artifact?.index ?? "—";
  ovCount.textContent = typeof artifact?.count === "number" ? String(artifact.count) : "—";
  ovTimeField.textContent = artifact?.time_field_used ?? "—";
}

function pickBestText(source) {
  if (!source || typeof source !== "object") return "";
  const candidates = ["message","summary","description","title","event.original","log.original"];
  for (const k of candidates) {
    const v = source[k];
    if (v && typeof v === "string" && v.trim().length > 0) return v;
  }
  for (const [k, v] of Object.entries(source)) {
    if (typeof v === "string" && v.trim().length > 0) return `${k}: ${v}`;
  }
  return "";
}

function pickTime(source, timeField) {
  if (!source || typeof source !== "object") return "";
  if (timeField && source[timeField]) return String(source[timeField]);
  const candidates = ["@timestamp","timestamp","time","date","published","published_at","created_at","created","event.created","event.ingested"];
  for (const k of candidates) {
    if (source[k]) return String(source[k]);
  }
  return "";
}

function renderHits(hits, timeField, filterText="") {
  const ft = (filterText || "").trim().toLowerCase();
  const filtered = !ft ? hits : hits.filter(h => {
    const s = (h?._source ? JSON.stringify(h._source) : "") + " " + (h?._id || "");
    return s.toLowerCase().includes(ft);
  });

  lastHits = filtered;

  if (!filtered.length) {
    hitsWrap.innerHTML = `<div class="hint">No hits to display.</div>`;
    return;
  }

  let html = "";
  for (const h of filtered) {
    const idx = h._index || "";
    const id = h._id || "";
    const score = h._score != null ? h._score : "";
    const src = h._source || {};
    const t = pickTime(src, timeField);
    const txt = pickBestText(src);

    html += `
      <div class="hit">
        <div class="hit-head">
          <div class="row gap">
            <span class="badge cyan mono">${escapeHtml(idx)}</span>
            <span class="badge violet mono">score: ${escapeHtml(score)}</span>
            ${t ? `<span class="badge ok mono">${escapeHtml(t)}</span>` : ``}
          </div>
          <div class="hit-actions">
            <button class="btn btn-small btn-ghost" data-open="${escapeHtml(id)}">View JSON</button>
          </div>
        </div>
        <div class="hit-body">${escapeHtml(txt || "(no message field found)")}</div>
        <div class="hit-meta">
          <span class="mono hint">_id: ${escapeHtml(id)}</span>
          <span class="mono hint">fields: ${escapeHtml(Object.keys(src).slice(0,8).join(", "))}${Object.keys(src).length>8 ? "…" : ""}</span>
        </div>
      </div>
    `;
  }

  hitsWrap.innerHTML = html;

  hitsWrap.querySelectorAll("[data-open]").forEach(btn => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-open");
      const hit = filtered.find(x => x._id === id);
      openModal(hit);
    });
  });
}

/* -------------------------
   Modal (fix: never show on boot)
------------------------- */
function openModal(hit) {
  if (!hit) return;
  const json = pretty(hit || {});
  lastModalJSON = json;
  modalTitle.textContent = `${hit?._index || "hit"} :: ${hit?._id || ""}`;
  modalBody.textContent = json;
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
}

function closeModal() {
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
}

/* -------------------------
   Tabs
------------------------- */
function setTab(tabName) {
  document.querySelectorAll(".tab").forEach(t => {
    t.classList.toggle("active", t.dataset.tab === tabName);
  });
  document.querySelectorAll(".pane").forEach(p => p.classList.remove("active"));
  const pane = el(`pane-${tabName}`);
  if (pane) pane.classList.add("active");
}

/* -------------------------
   API / Health
------------------------- */
async function checkHealth() {
  try {
    const r = await fetch("/api/health");
    const j = await r.json();
    if (j.ok) {
      const name = (j.es && j.es.cluster_name) ? j.es.cluster_name : "ok";
      esLabel.textContent = `ES: ${name}`;
      dotEs.classList.remove("dot-warn");
      dotEs.classList.add("dot-ok");
    } else {
      esLabel.textContent = "ES: error";
      dotEs.classList.remove("dot-ok");
      dotEs.classList.add("dot-warn");
    }
  } catch (e) {
    esLabel.textContent = "ES: unreachable";
    dotEs.classList.remove("dot-ok");
    dotEs.classList.add("dot-warn");
  }
}

function updatePanelsFromResponse(data) {
  lastResponse = data;
  rawBox.textContent = pretty(data);

  const artifact = data.artifact || {};
  const trace = data.trace || [];
  traceBox.textContent = pretty(trace);

  let queryObj = null;

  if (artifact && artifact.dsl) {
    queryObj = { index: artifact.index, dsl: artifact.dsl, stage: artifact.stage, time_field_used: artifact.time_field_used };
    queryUsedEl.textContent = pretty(queryObj);
    updateOverview(artifact);
    renderHits(artifact.hits || [], artifact.time_field_used, hitsFilter.value);
  } else if (artifact && artifact.query && artifact.rows) {
    queryObj = { query: artifact.query, filter: artifact.filter, time_field_used: artifact.time_field_used };
    queryUsedEl.textContent = pretty(queryObj);
    ovStage.textContent = "esql";
    ovIndex.textContent = "(from ES|QL query)";
    ovCount.textContent = String((artifact.rows || []).length);
    ovTimeField.textContent = artifact.time_field_used || "—";

    const cols = artifact.columns || [];
    const rows = artifact.rows || [];
    const pseudo = rows.map((r, i) => {
      const src = {};
      cols.forEach((c, j) => src[c] = r[j]);
      return {_index: "esql", _id: String(i), _score: "", _source: src};
    });
    renderHits(pseudo, artifact.time_field_used, hitsFilter.value);
  } else {
    queryUsedEl.textContent = "";
    updateOverview({});
    renderHits([], null, "");
  }

  lastQueryObj = queryObj;
}

/* -------------------------
   Send message
------------------------- */
async function sendMessage() {
  const msg = inputEl.value.trim();
  if (!msg) return;

  addMessage("user", msg);
  inputEl.value = "";
  setLoading(true);
  reqMeta.textContent = `req @ ${new Date().toISOString()}`;

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ message: msg, conversation_id: conversationId })
    });

    const data = await resp.json();

    if (data.conversation_id) setConversationId(data.conversation_id);

    if (!resp.ok || data.error) {
      addMessage("assistant", `**Error:** ${data.error || resp.statusText}`);
      showToast(`API error: ${data.error || resp.statusText}`);
      setLoading(false);
      return;
    }

    addMessage("assistant", data.answer || "(no answer)");
    updatePanelsFromResponse(data);

  } catch (e) {
    addMessage("assistant", `**Network error:** ${String(e)}`);
    showToast("Network error.");
  } finally {
    setLoading(false);
  }
}

/* -------------------------
   UI actions
------------------------- */
function clearUI() {
  messagesEl.innerHTML = "";
  hitsWrap.innerHTML = "";
  queryUsedEl.textContent = "";
  traceBox.textContent = "";
  rawBox.textContent = "";
  updateOverview({});
  lastResponse = null;
  lastQueryObj = null;
  lastHits = [];
}

function wireTabs() {
  document.querySelectorAll(".tab").forEach(t => {
    t.addEventListener("click", () => setTab(t.dataset.tab));
  });
}

function wireQuickPrompts() {
  document.querySelectorAll(".qbtn").forEach(b => {
    b.addEventListener("click", () => {
      inputEl.value = b.dataset.q || "";
      inputEl.focus();
    });
  });
}

function wireButtons() {
  btnCopyConv.addEventListener("click", () => copyText(conversationId || ""));
  btnResetConv.addEventListener("click", () => {
    localStorage.removeItem("conversation_id");
    setConversationId(null);
    clearUI();
    showToast("Conversation reset.");
  });

  btnClearUI.addEventListener("click", () => {
    clearUI();
    showToast("UI cleared.");
  });

  btnCopyQuery.addEventListener("click", () => copyText(queryUsedEl.textContent || ""));
  btnPrettyQuery.addEventListener("click", () => {
    try {
      const obj = JSON.parse(queryUsedEl.textContent || "{}");
      queryUsedEl.textContent = pretty(obj);
    } catch (_) {}
  });

  btnCopyLastQuery.addEventListener("click", () => copyText(pretty(lastQueryObj || {})));
  btnCopyLastRaw.addEventListener("click", () => copyText(pretty(lastResponse || {})));

  btnExportHits.addEventListener("click", () => {
    const blob = new Blob([pretty(lastHits || [])], {type: "application/json"});
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "hits.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  });

  // modal
  modalBackdrop.addEventListener("click", closeModal);
  btnCloseModal.addEventListener("click", closeModal);
  btnCopyModal.addEventListener("click", () => copyText(lastModalJSON || ""));
}

function wireComposer() {
  sendBtn.addEventListener("click", sendMessage);

  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  hitsFilter.addEventListener("input", () => {
    if (!lastResponse) return;
    const art = lastResponse.artifact || {};
    if (art && art.hits) renderHits(art.hits || [], art.time_field_used, hitsFilter.value);
  });
}

/* -------------------------
   Init
------------------------- */
async function init() {
  setConversationId(conversationId);

  // ✅ Force-hide modal on boot (fixes the “Hit” popup)
  closeModal();

  wireTabs();
  wireQuickPrompts();
  wireButtons();
  wireComposer();

  await checkHealth();
  setInterval(checkHealth, 20_000);

  setTab("overview");
}

init();

