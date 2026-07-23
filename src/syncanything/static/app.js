const state = { source: "", query: "", activeSession: null };
const searchInput = document.querySelector("#search");
const resultsNode = document.querySelector("#results");
const resultTitle = document.querySelector("#result-title");
const resultCount = document.querySelector("#result-count");
const dialog = document.querySelector("#session-dialog");
let searchTimer;

const sourceNames = { claude: "Claude Code", codex: "Codex", kimi: "Kimi Code", pi: "Pi", citeanything: "CiteAnything" };

function escapeHtml(value = "") {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function safeSnippet(value = "") {
  return escapeHtml(value)
    .replaceAll("&lt;mark&gt;", "<mark>")
    .replaceAll("&lt;/mark&gt;", "</mark>");
}

function shortDate(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value.slice(0, 10) : new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function loadStatus() {
  const status = await getJson("/api/status");
  document.querySelector("#status").textContent = `${status.sessions} 个会话 · ${status.messages} 条消息 · 仅存于本机`;
}

async function loadResults() {
  resultsNode.innerHTML = '<div class="empty">正在检索…</div>';
  const params = new URLSearchParams({ q: state.query, limit: "60" });
  if (state.source) params.set("source", state.source);
  const data = await getJson(`/api/sessions?${params}`);
  resultTitle.textContent = state.query ? `“${state.query}”的结果` : "最近的会话";
  resultCount.textContent = `${data.results.length} 个`;
  renderResults(data.results);
}

function renderResults(results) {
  resultsNode.innerHTML = "";
  if (!results.length) {
    resultsNode.innerHTML = '<div class="empty">没有找到匹配的会话。试试更接近原话的关键词。</div>';
    return;
  }
  const template = document.querySelector("#result-template");
  for (const item of results) {
    const card = template.content.firstElementChild.cloneNode(true);
    card.querySelector(".source-badge").textContent = sourceNames[item.source] || item.source;
    card.querySelector("time").textContent = shortDate(item.updated_at);
    card.querySelector(".message-count").textContent = `${item.message_count} 条消息`;
    card.querySelector("h3").textContent = item.title;
    const snippet = card.querySelector(".snippet");
    if (item.snippet) snippet.innerHTML = safeSnippet(item.snippet.replaceAll("\n", " "));
    else snippet.textContent = "打开查看会话内容";
    card.querySelector(".cwd").textContent = item.cwd || item.canonical_url || item.source_path;
    card.addEventListener("click", () => openSession(item.id));
    card.addEventListener("keydown", event => { if (event.key === "Enter") openSession(item.id); });
    resultsNode.append(card);
  }
}

async function openSession(sessionId) {
  const session = await getJson(`/api/session?id=${encodeURIComponent(sessionId)}`);
  state.activeSession = session;
  document.querySelector("#dialog-source").textContent = sourceNames[session.source] || session.source;
  document.querySelector("#dialog-title").textContent = session.title;
  document.querySelector("#dialog-id").textContent = session.id;
  const conversation = document.querySelector("#conversation");
  conversation.innerHTML = "";
  for (const message of session.messages) {
    const node = document.createElement("section");
    node.className = `message ${message.role}`;
    const role = document.createElement("div");
    role.className = "message-role";
    role.textContent = message.role === "user" ? "User" : "Assistant";
    const text = document.createElement("div");
    text.className = "message-text";
    text.textContent = message.text;
    node.append(role, text);
    conversation.append(node);
  }
  dialog.showModal();
}

searchInput.addEventListener("input", () => {
  state.query = searchInput.value.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadResults, 180);
});

document.querySelectorAll(".filter").forEach(button => button.addEventListener("click", () => {
  document.querySelector(".filter.active").classList.remove("active");
  button.classList.add("active");
  state.source = button.dataset.source;
  loadResults();
}));

document.querySelector("#reindex").addEventListener("click", async event => {
  event.currentTarget.disabled = true;
  event.currentTarget.textContent = "刷新中…";
  try {
    await getJson("/api/reindex", { method: "POST" });
    await Promise.all([loadStatus(), loadResults()]);
  } finally {
    event.currentTarget.disabled = false;
    event.currentTarget.textContent = "刷新索引";
  }
});

document.querySelector("#close-dialog").addEventListener("click", () => dialog.close());
document.querySelector("#copy-reference").addEventListener("click", async event => {
  const session = state.activeSession;
  if (!session) return;
  const location = session.metadata?.canonical_url || session.source_path;
  const reference = `请先通过 SyncAnything 读取这个会话，再继续当前任务。\nSession: ${session.id}\nURI: ${session.uri}\nSource: ${location}`;
  await navigator.clipboard.writeText(reference);
  event.currentTarget.textContent = "已复制";
  setTimeout(() => { event.currentTarget.textContent = "复制给智能体的引用"; }, 1300);
});

document.addEventListener("keydown", event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    searchInput.focus();
  }
});

Promise.all([loadStatus(), loadResults()]).catch(error => {
  resultsNode.innerHTML = `<div class="empty">读取失败：${escapeHtml(error.message)}</div>`;
});
