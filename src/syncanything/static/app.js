const LANGUAGE_KEY = "syncanything.language";
const LANGUAGES = ["zh-Hans", "en"];

const translations = {
  "zh-Hans": {
    "brand.home": "SyncAnything 首页",
    "language.switch": "Switch to English",
    "language.button": "EN",
    "action.connect": "连接",
    "action.reindex": "刷新索引",
    "action.reindexing": "刷新中…",
    "action.close": "关闭",
    "action.copy_reference": "复制给智能体的引用",
    "action.copied": "已复制",
    "status.loading": "正在读取本地索引…",
    "status.summary": "{sessions} 个会话 · {messages} 条消息 · 仅存于本机",
    "hero.title": "找到那次对话，<br /><span>指给任何智能体。</span>",
    "hero.intro": "统一检索 Claude Code、Codex、Kimi Code、Pi 和 CiteAnything 的宝贵上下文。",
    "search.placeholder": "搜索决定、问题、项目或原话…",
    "filters.label": "按来源筛选",
    "filter.all": "全部",
    "stats.title": "本地存储",
    "stats.subtitle": "全部数据只存在这台机器上",
    "stats.sessions": "会话",
    "stats.messages": "消息",
    "stats.characters": "总字数",
    "stats.characters.detail": "{words} 个词",
    "stats.tokens": "估算 Token",
    "stats.tokens.detail": "按中日韩 1.5 字 / 拉丁 4 字符估算",
    "stats.storage": "索引占用",
    "stats.storage.detail": "正文 {text}，索引膨胀 {ratio} 倍",
    "stats.books": "相当于",
    "stats.books.value": "{count} 本",
    "stats.books.note": "换算参考：{list}",
    "stats.books.item": "{count} 本《{title}》",
    "results.recent": "最近的会话",
    "results.query": "“{query}”的结果",
    "results.searching": "正在检索…",
    "results.empty": "没有找到匹配的会话。试试更接近原话的关键词。",
    "results.count": "{total} 个",
    "results.count_partial": "显示 {shown} / 共 {total} 个",
    "results.open": "读取会话 →",
    "results.messages": "{count} 条消息",
    "results.no_snippet": "打开查看会话内容",
    "results.failed": "读取失败：{message}",
    "time.unknown": "时间未知",
    "role.user": "User",
    "role.assistant": "Assistant",
    "connections.title": "连接账号",
    "connections.help": "每个站点和账号授权一次。密钥保存在本机系统安全存储中，不会进入会话索引。",
    "connections.empty": "尚未连接 CiteAnything 账号",
    "connections.authorized": "已授权",
    "connections.missing_key": "密钥缺失",
    "connections.remove": "移除",
    "connections.site": "站点",
    "connections.site.china": "中国站 · citeanything.cn",
    "connections.site.international": "国际站 · citeanything.app",
    "connections.site.custom": "自定义",
    "connections.name": "连接名称",
    "connections.base_url": "站点地址",
    "connections.key": "SyncAnything API key",
    "connections.hint": "请在对应 CiteAnything 站点点击“Connect SyncAnything”生成专用密钥。",
    "connections.submit": "验证并连接",
    "connections.validating": "正在验证并同步…",
    "connections.connected": "连接成功，正在后台同步；可以关闭此窗口",
    "connections.syncing": "已授权，正在后台同步会话…",
    "connections.synced": "同步完成",
    "connections.default_name.china": "CiteAnything 中国站",
    "connections.default_name.international": "CiteAnything 国际站",
    "connections.default_name.custom": "CiteAnything",
    "error.key_rejected": "CiteAnything 拒绝了该密钥（HTTP {status}）",
    "error.unreachable": "无法连接 CiteAnything：{detail}",
    "error.invalid_request": "请求内容无效",
    "reference.instruction": "请先通过 SyncAnything 读取这个会话，再继续当前任务。",
  },
  en: {
    "brand.home": "SyncAnything home",
    "language.switch": "切换到简体中文",
    "language.button": "中文",
    "action.connect": "Connect",
    "action.reindex": "Reindex",
    "action.reindexing": "Reindexing…",
    "action.close": "Close",
    "action.copy_reference": "Copy reference for an agent",
    "action.copied": "Copied",
    "status.loading": "Reading the local index…",
    "status.summary": "{sessions} sessions · {messages} messages · local only",
    "hero.title": "Find that conversation,<br /><span>point any agent to it.</span>",
    "hero.intro": "Search the context you built in Claude Code, Codex, Kimi Code, Pi, and CiteAnything from one place.",
    "search.placeholder": "Search decisions, problems, projects, or exact words…",
    "filters.label": "Filter by source",
    "filter.all": "All",
    "stats.title": "Local storage",
    "stats.subtitle": "Everything here lives on this machine only",
    "stats.sessions": "Sessions",
    "stats.messages": "Messages",
    "stats.characters": "Words",
    "stats.characters.detail": "{characters} characters",
    "stats.tokens": "Estimated tokens",
    "stats.tokens.detail": "Estimated at 1.5 CJK / 4 Latin characters per token",
    "stats.storage": "Index on disk",
    "stats.storage.detail": "{text} of text, {ratio}x index overhead",
    "stats.books": "Equivalent to",
    "stats.books.value": "{count}x",
    "stats.books.note": "For scale: {list}",
    "stats.books.item": "{count}x {title}",
    "results.recent": "Recent sessions",
    "results.query": "Results for “{query}”",
    "results.searching": "Searching…",
    "results.empty": "No sessions matched. Try wording closer to what was actually said.",
    "results.count": "{total}",
    "results.count_partial": "Showing {shown} of {total}",
    "results.open": "Read session →",
    "results.messages": "{count} messages",
    "results.no_snippet": "Open to read the conversation",
    "results.failed": "Could not load: {message}",
    "time.unknown": "Time unknown",
    "role.user": "User",
    "role.assistant": "Assistant",
    "connections.title": "Connect an account",
    "connections.help": "Authorize each site and account once. Keys live in this machine's secure storage and never enter the session index.",
    "connections.empty": "No CiteAnything account connected yet",
    "connections.authorized": "authorized",
    "connections.missing_key": "key missing",
    "connections.remove": "Remove",
    "connections.site": "Site",
    "connections.site.china": "China · citeanything.cn",
    "connections.site.international": "International · citeanything.app",
    "connections.site.custom": "Custom",
    "connections.name": "Connection name",
    "connections.base_url": "Site URL",
    "connections.key": "SyncAnything API key",
    "connections.hint": "In the matching CiteAnything site, use “Connect SyncAnything” to create a dedicated key.",
    "connections.submit": "Verify and connect",
    "connections.validating": "Verifying and syncing…",
    "connections.connected": "Connected. Syncing in the background; you can close this window.",
    "connections.syncing": "Authorized, syncing sessions in the background…",
    "connections.synced": "Sync complete",
    "connections.default_name.china": "CiteAnything China",
    "connections.default_name.international": "CiteAnything International",
    "connections.default_name.custom": "CiteAnything",
    "error.key_rejected": "CiteAnything rejected the key (HTTP {status})",
    "error.unreachable": "Could not reach CiteAnything: {detail}",
    "error.invalid_request": "Invalid request",
    "reference.instruction": "Read this session through SyncAnything before continuing the current task.",
  },
};

function detectLanguage() {
  const saved = localStorage.getItem(LANGUAGE_KEY);
  if (LANGUAGES.includes(saved)) return saved;
  const preferred = navigator.languages || [navigator.language || "en"];
  return preferred.some(tag => tag.toLowerCase().startsWith("zh")) ? "zh-Hans" : "en";
}

const state = {
  source: "",
  query: "",
  activeSession: null,
  language: detectLanguage(),
  status: null,
  results: null,
  resultQuery: "",
  resultTotal: 0,
  connections: null,
};

const searchInput = document.querySelector("#search");
const resultsNode = document.querySelector("#results");
const resultTitle = document.querySelector("#result-title");
const resultCount = document.querySelector("#result-count");
const dialog = document.querySelector("#session-dialog");
const connectionsDialog = document.querySelector("#connections-dialog");
let searchTimer;

const sourceNames = { claude: "Claude Code", codex: "Codex", kimi: "Kimi Code", pi: "Pi", citeanything: "CiteAnything" };

function t(key, params = {}) {
  const table = translations[state.language] || translations.en;
  const template = table[key] ?? translations.en[key] ?? key;
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match,
  );
}

function formatNumber(value) {
  return new Intl.NumberFormat(state.language).format(value ?? 0);
}

function formatBytes(value) {
  const units = ["B", "KB", "MB", "GB"];
  let size = value ?? 0;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  const rounded = unit === 0 ? size : Number(size.toFixed(1));
  return `${formatNumber(rounded)} ${units[unit]}`;
}

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
  if (!value) return t("time.unknown");
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value.slice(0, 10);
  return new Intl.DateTimeFormat(state.language, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

function describeError(error) {
  // The server sends a stable code so the message can be rendered in the
  // reader's language; its English `error` text is the fallback.
  const payload = error?.payload;
  if (payload?.code) {
    const translated = t(`error.${payload.code}`, {
      status: payload.status ?? "",
      detail: payload.detail ?? "",
    });
    if (translated !== `error.${payload.code}`) return translated;
  }
  return error?.message ?? String(error);
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    const failure = new Error(data.error || `HTTP ${response.status}`);
    failure.payload = data;
    throw failure;
  }
  return data;
}

function applyTranslations(root = document) {
  for (const node of root.querySelectorAll("[data-i18n]")) {
    node.textContent = t(node.dataset.i18n);
  }
  // Only the bundled dictionary feeds this, never indexed conversation text.
  for (const node of root.querySelectorAll("[data-i18n-html]")) {
    node.innerHTML = t(node.dataset.i18nHtml);
  }
  for (const node of root.querySelectorAll("[data-i18n-placeholder]")) {
    node.placeholder = t(node.dataset.i18nPlaceholder);
  }
  for (const node of root.querySelectorAll("[data-i18n-aria-label]")) {
    node.setAttribute("aria-label", t(node.dataset.i18nAriaLabel));
  }
}

function applyLanguage() {
  document.documentElement.lang = state.language;
  document.querySelector("#language-toggle").textContent = t("language.button");
  applyTranslations();
  renderStatus();
  renderStats();
  renderResultHeading();
  if (state.results) renderResults(state.results, state.resultQuery);
  if (state.connections) renderConnections(state.connections);
  syncConnectionName();
}

function renderStatus() {
  const node = document.querySelector("#status");
  if (!state.status) {
    node.textContent = t("status.loading");
    return;
  }
  node.textContent = t("status.summary", {
    sessions: formatNumber(state.status.sessions),
    messages: formatNumber(state.status.messages),
  });
}

function renderStats() {
  const section = document.querySelector("#stats-section");
  const grid = document.querySelector("#stat-grid");
  const note = document.querySelector("#stat-note");
  const status = state.status;
  if (!status) {
    section.hidden = true;
    return;
  }
  const books = status.books?.[state.language === "zh-Hans" ? "zh" : "en"] ?? [];
  const primaryBook = books[0];
  const ratio = status.text_bytes
    ? (status.storage_bytes / status.text_bytes).toFixed(1)
    : "0";

  const cards = [
    { label: t("stats.sessions"), value: formatNumber(status.sessions) },
    { label: t("stats.messages"), value: formatNumber(status.messages) },
    {
      label: t("stats.characters"),
      value: formatNumber(state.language === "zh-Hans" ? status.characters : status.words),
      detail: t("stats.characters.detail", {
        words: formatNumber(status.words),
        characters: formatNumber(status.characters),
      }),
    },
    {
      label: t("stats.tokens"),
      value: `~${formatNumber(status.tokens)}`,
      detail: t("stats.tokens.detail"),
    },
    {
      label: t("stats.storage"),
      value: formatBytes(status.storage_bytes),
      detail: t("stats.storage.detail", { text: formatBytes(status.text_bytes), ratio }),
    },
  ];
  if (primaryBook) {
    cards.push({
      label: t("stats.books"),
      value: t("stats.books.value", { count: primaryBook.equivalent }),
      detail: state.language === "zh-Hans" ? `《${primaryBook.title}》` : primaryBook.title,
    });
  }

  const template = document.querySelector("#stat-template");
  grid.innerHTML = "";
  for (const card of cards) {
    const node = template.content.firstElementChild.cloneNode(true);
    node.querySelector(".stat-label").textContent = card.label;
    node.querySelector(".stat-value").textContent = card.value;
    const detail = node.querySelector(".stat-detail");
    if (card.detail) detail.textContent = card.detail;
    else detail.remove();
    grid.append(node);
  }

  note.textContent = books.length
    ? t("stats.books.note", {
        list: books
          .map(book => t("stats.books.item", { count: book.equivalent, title: book.title }))
          .join(" · "),
      })
    : "";
  section.hidden = false;
}

function renderResultHeading() {
  resultTitle.textContent = state.resultQuery
    ? t("results.query", { query: state.resultQuery })
    : t("results.recent");
  if (!state.results) {
    resultCount.textContent = "";
    return;
  }
  resultCount.textContent =
    state.resultTotal > state.results.length
      ? t("results.count_partial", {
          shown: formatNumber(state.results.length),
          total: formatNumber(state.resultTotal),
        })
      : t("results.count", { total: formatNumber(state.resultTotal) });
}

async function loadConnections() {
  const data = await getJson("/api/connections");
  state.connections = data.citeanything;
  renderConnections(state.connections);
}

function renderConnections(items) {
  const list = document.querySelector("#connections-list");
  list.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "connection-empty";
    empty.textContent = t("connections.empty");
    list.append(empty);
    return;
  }
  for (const item of items) {
    const node = document.createElement("div");
    node.className = "connection-item";
    const text = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.name;
    const detail = document.createElement("small");
    detail.textContent = `${item.base_url} · ${item.connected ? t("connections.authorized") : t("connections.missing_key")}`;
    text.append(title, detail);
    const remove = document.createElement("button");
    remove.className = "quiet-button";
    remove.textContent = t("connections.remove");
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
    const sync = await getJson("/api/sync");
    if (!sync.running) return sync;
    if (onProgress) onProgress();
    await new Promise(resolve => setTimeout(resolve, 1200));
  }
}

async function loadStatus() {
  state.status = await getJson("/api/status");
  renderStatus();
  renderStats();
}

async function loadResults() {
  resultsNode.innerHTML = "";
  const pending = document.createElement("div");
  pending.className = "empty";
  pending.textContent = t("results.searching");
  resultsNode.append(pending);

  const requestedQuery = state.query;
  const requestedSource = state.source;
  const params = new URLSearchParams({ q: requestedQuery, limit: "60" });
  if (requestedSource) params.set("source", requestedSource);
  const data = await getJson(`/api/sessions?${params}`);
  if (requestedQuery !== state.query || requestedSource !== state.source) return;
  state.results = data.results;
  state.resultQuery = requestedQuery;
  state.resultTotal = data.total;
  renderResultHeading();
  renderResults(data.results, requestedQuery);
}

function renderResults(results, resultQuery = "") {
  resultsNode.innerHTML = "";
  if (!results.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = t("results.empty");
    resultsNode.append(empty);
    return;
  }
  const template = document.querySelector("#result-template");
  for (const item of results) {
    const card = template.content.firstElementChild.cloneNode(true);
    card.querySelector(".source-badge").textContent = sourceNames[item.source] || item.source;
    card.querySelector("time").textContent = shortDate(item.updated_at);
    card.querySelector(".message-count").textContent = t("results.messages", { count: formatNumber(item.message_count) });
    card.querySelector("h3").textContent = item.title;
    card.querySelector(".open-label").textContent = t("results.open");
    const snippet = card.querySelector(".snippet");
    if (item.snippet) snippet.innerHTML = safeSnippet(item.snippet.replaceAll("\n", " "));
    else snippet.textContent = t("results.no_snippet");
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
    role.textContent = message.role === "user" ? t("role.user") : t("role.assistant");
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

const CONNECTION_SITES = ["china", "international", "custom"];

function isUntouchedConnectionName(value) {
  if (!value) return true;
  return LANGUAGES.some(language =>
    CONNECTION_SITES.some(
      site => translations[language][`connections.default_name.${site}`] === value,
    ),
  );
}

function syncConnectionName() {
  const site = document.querySelector("#connection-site").value;
  document.querySelector("#base-url-label").hidden = site !== "custom";
  // Only replace a name the reader has not written themselves, so switching
  // language or site never discards a typed-in connection name.
  const field = document.querySelector("#connection-name");
  if (isUntouchedConnectionName(field.value)) {
    field.value = t(`connections.default_name.${site}`);
  }
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

document.querySelector("#language-toggle").addEventListener("click", () => {
  state.language = state.language === "zh-Hans" ? "en" : "zh-Hans";
  localStorage.setItem(LANGUAGE_KEY, state.language);
  applyLanguage();
});

document.querySelector("#reindex").addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = t("action.reindexing");
  try {
    await getJson("/api/reindex", { method: "POST" });
    const sync = await waitForSync();
    if (sync.error) throw new Error(sync.error);
    await Promise.all([loadStatus(), loadResults()]);
  } finally {
    button.disabled = false;
    button.textContent = t("action.reindex");
  }
});

document.querySelector("#close-dialog").addEventListener("click", () => dialog.close());
document.querySelector("#connections-button").addEventListener("click", async () => {
  connectionsDialog.showModal();
  await loadConnections();
});
document.querySelector("#close-connections").addEventListener("click", () => connectionsDialog.close());

document.querySelector("#connection-site").addEventListener("change", syncConnectionName);

document.querySelector("#connection-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type="submit"]');
  const message = document.querySelector("#connection-message");
  button.disabled = true;
  button.textContent = t("connections.validating");
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
    message.textContent = t("connections.connected");
    await loadConnections();
    const sync = await waitForSync(() => {
      message.textContent = t("connections.syncing");
    });
    if (sync.error) throw new Error(sync.error);
    message.textContent = t("connections.synced");
    await Promise.all([loadStatus(), loadResults()]);
  } catch (error) {
    message.textContent = describeError(error);
  } finally {
    button.disabled = false;
    button.textContent = t("connections.submit");
  }
});

document.querySelector("#copy-reference").addEventListener("click", async event => {
  const session = state.activeSession;
  if (!session) return;
  const location = session.metadata?.canonical_url || session.source_path;
  const reference = `${t("reference.instruction")}\nSession: ${session.id}\nURI: ${session.uri}\nSource: ${location}`;
  await navigator.clipboard.writeText(reference);
  event.currentTarget.textContent = t("action.copied");
  setTimeout(() => { event.currentTarget.textContent = t("action.copy_reference"); }, 1300);
});

document.addEventListener("keydown", event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    searchInput.focus();
  }
});

applyLanguage();
Promise.all([loadStatus(), loadResults()]).catch(error => {
  resultsNode.innerHTML = "";
  const failure = document.createElement("div");
  failure.className = "empty";
  failure.textContent = t("results.failed", { message: describeError(error) });
  resultsNode.append(failure);
});
