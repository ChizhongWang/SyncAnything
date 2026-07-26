const state = { source: "", query: "", activeSession: null };
const searchInput = document.querySelector("#search");
const resultsNode = document.querySelector("#results");
const resultTitle = document.querySelector("#result-title");
const resultCount = document.querySelector("#result-count");
const dialog = document.querySelector("#session-dialog");
const connectionsDialog = document.querySelector("#connections-dialog");
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

function appendHighlightedText(node, value, query) {
  const position = query ? value.toLocaleLowerCase().indexOf(query.toLocaleLowerCase()) : -1;
  if (position < 0) {
    node.textContent = value;
    return null;
  }
  node.append(document.createTextNode(value.slice(0, position)));
  const mark = document.createElement("mark");
  mark.className = "match-highlight";
  mark.textContent = value.slice(position, position + query.length);
  node.append(mark, document.createTextNode(value.slice(position + query.length)));
  return mark;
}

function centerDialogTarget(target) {
  if (!target || !dialog.open) return;
  const dialogRect = dialog.getBoundingClientRect();
  const targetRect = target.getBoundingClientRect();
  dialog.scrollTo({
    top: Math.max(
      0,
      dialog.scrollTop +
        targetRect.top +
        targetRect.height / 2 -
        dialogRect.top -
        dialogRect.height / 2,
    ),
    behavior: "auto",
  });
}

function shortDate(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value.slice(0, 10) : new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function loadConnections() {
  const data = await getJson("/api/connections");
  const list = document.querySelector("#connections-list");
  list.innerHTML = "";
  if (!data.citeanything.length) {
    list.innerHTML = '<div class="connection-empty">尚未连接 CiteAnything 账号</div>';
    return;
  }
  for (const item of data.citeanything) {
    const node = document.createElement("div");
    node.className = "connection-item";
    const text = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.name;
    const detail = document.createElement("small");
    detail.textContent = `${item.base_url} · ${item.connected ? "已授权" : "密钥缺失"}`;
    text.append(title, detail);
    const remove = document.createElement("button");
    remove.className = "quiet-button";
    remove.textContent = "移除";
    remove.type = "button";
    remove.addEventListener("click", async () => {
      await getJson(`/api/connections/citeanything/${encodeURIComponent(item.id)}`, { method: "DELETE" });
      await loadConnections();
    });
    node.append(text, remove);
    list.append(node);
  }
}

async function waitForSync(onProgress) {
  for (;;) {
    const state = await getJson("/api/sync");
    if (!state.running) return state;
    if (onProgress) onProgress();
    await new Promise(resolve => setTimeout(resolve, 1200));
  }
}

async function loadStatus() {
  const status = await getJson("/api/status");
  document.querySelector("#status").textContent = `${status.sessions} 个会话 · ${status.messages} 条消息 · 仅存于本机`;
}

async function loadResults() {
  resultsNode.innerHTML = '<div class="empty">正在检索…</div>';
  const requestedQuery = state.query;
  const requestedSource = state.source;
  const params = new URLSearchParams({ q: requestedQuery, limit: "60" });
  if (requestedSource) params.set("source", requestedSource);
  const data = await getJson(`/api/sessions?${params}`);
  if (requestedQuery !== state.query || requestedSource !== state.source) return;
  resultTitle.textContent = requestedQuery ? `“${requestedQuery}”的结果` : "最近的会话";
  resultCount.textContent =
    data.total > data.results.length
      ? `显示 ${data.results.length} / 共 ${data.total} 个`
      : `${data.total} 个`;
  renderResults(data.results, requestedQuery);
}

function renderResults(results, resultQuery = "") {
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
    card.addEventListener("click", () => openSession(item, resultQuery));
    card.addEventListener("keydown", event => {
      if (event.key === "Enter") openSession(item, resultQuery);
    });
    resultsNode.append(card);
  }
}

async function openSession(item, query) {
  const params = new URLSearchParams({ id: item.id });
  if (Number.isInteger(item.match_ordinal)) {
    params.set("focus_ordinal", String(item.match_ordinal));
  }
  if (query) params.set("focus_query", query);
  const session = await getJson(`/api/session?${params}`);
  state.activeSession = session;
  document.querySelector("#dialog-source").textContent = sourceNames[session.source] || session.source;
  document.querySelector("#dialog-title").textContent = session.title;
  document.querySelector("#dialog-id").textContent = session.id;
  const conversation = document.querySelector("#conversation");
  conversation.innerHTML = "";
  let targetMessage = null;
  let targetMark = null;
  for (const message of session.messages) {
    const node = document.createElement("section");
    node.className = `message ${message.role}`;
    node.dataset.ordinal = String(message.ordinal);
    const role = document.createElement("div");
    role.className = "message-role";
    role.textContent = message.role === "user" ? "User" : "Assistant";
    const text = document.createElement("div");
    text.className = "message-text";
    const isTarget =
      message.ordinal === item.match_ordinal ||
      (targetMessage === null && query && message.text.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
    if (isTarget && targetMessage === null) {
      node.classList.add("match-target");
      targetMessage = node;
      targetMark = appendHighlightedText(text, message.text, query);
    } else {
      text.textContent = message.text;
    }
    node.append(role, text);
    conversation.append(node);
  }
  dialog.scrollTop = 0;
  dialog.showModal();
  requestAnimationFrame(() => {
    requestAnimationFrame(() => centerDialogTarget(targetMark || targetMessage));
  });
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
    const sync = await waitForSync();
    if (sync.error) throw new Error(sync.error);
    await Promise.all([loadStatus(), loadResults()]);
  } finally {
    event.currentTarget.disabled = false;
    event.currentTarget.textContent = "刷新索引";
  }
});

document.querySelector("#close-dialog").addEventListener("click", () => dialog.close());
document.querySelector("#connections-button").addEventListener("click", async () => {
  connectionsDialog.showModal();
  await loadConnections();
});
document.querySelector("#close-connections").addEventListener("click", () => connectionsDialog.close());

document.querySelector("#connection-site").addEventListener("change", event => {
  const site = event.currentTarget.value;
  document.querySelector("#base-url-label").hidden = site !== "custom";
  document.querySelector("#connection-name").value =
    site === "china" ? "CiteAnything 中国站" :
    site === "international" ? "CiteAnything 国际站" : "CiteAnything";
});

document.querySelector("#connection-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type="submit"]');
  const message = document.querySelector("#connection-message");
  button.disabled = true;
  button.textContent = "正在验证并同步…";
  message.textContent = "";
  try {
    const site = document.querySelector("#connection-site").value;
    const payload = {
      site,
      name: document.querySelector("#connection-name").value.trim(),
      api_key: document.querySelector("#connection-key").value.trim(),
    };
    if (site === "custom") payload.base_url = document.querySelector("#connection-base-url").value.trim();
    await getJson("/api/connections/citeanything", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    document.querySelector("#connection-key").value = "";
    message.textContent = "连接成功，正在后台同步；可以关闭此窗口";
    await loadConnections();
    const sync = await waitForSync(() => {
      message.textContent = "已授权，正在后台同步会话…";
    });
    if (sync.error) throw new Error(sync.error);
    message.textContent = "同步完成";
    await Promise.all([loadStatus(), loadResults()]);
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "验证并连接";
  }
});

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
