const state = {
  tokens: [],
  updatedAt: null,
  timer: null,
  loading: false,
  logs: [],
};
const demoMode = new URLSearchParams(window.location.search).get("demo") === "1";

const elements = {
  grid: document.querySelector("#tokenGrid"),
  empty: document.querySelector("#emptyState"),
  search: document.querySelector("#searchInput"),
  sort: document.querySelector("#sortSelect"),
  refresh: document.querySelector("#refreshButton"),
  auto: document.querySelector("#autoRefresh"),
  banner: document.querySelector("#bannerText"),
  updated: document.querySelector("#lastUpdated"),
  connection: document.querySelector("#connectionState"),
  count: document.querySelector("#resultCount"),
  logs: document.querySelector("#logEntries"),
  logCount: document.querySelector("#logCount"),
};

const number = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 2 });

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function shortAddress(value) {
  if (!value || value.length < 13) return value || "地址未知";
  return `${value.slice(0, 7)}…${value.slice(-5)}`;
}

function money(value) {
  if (value === null || value === undefined) return "—";
  if (Math.abs(value) < 0.01) return `$${Number(value).toPrecision(3)}`;
  return `$${compact.format(value)}`;
}

function change(value) {
  if (value === null || value === undefined) return '<span class="metric-value">—</span>';
  const className = value >= 0 ? "positive" : "negative";
  const prefix = value > 0 ? "+" : "";
  return `<span class="metric-value ${className}">${prefix}${number.format(value)}%</span>`;
}

function age(iso) {
  if (!iso) return "时间未知";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时`;
  return `${Math.floor(seconds / 86400)} 天`;
}

function log(message, type = "ok") {
  state.logs.unshift({ time: new Date(), message, type });
  state.logs = state.logs.slice(0, 8);
  elements.logs.innerHTML = state.logs.map((entry) => `
    <div class="log-entry ${entry.type === "error" ? "error" : ""}">
      <time>${entry.time.toLocaleString("zh-CN", { hour12: false })}</time>
      <strong>${entry.type === "error" ? "FEED ERROR" : "BSC RADAR"}</strong>
      <span>${escapeHtml(entry.message)}</span>
    </div>`).join("");
  elements.logCount.textContent = `${state.logs.length} 条`;
}

function skeletons() {
  elements.empty.hidden = true;
  elements.grid.innerHTML = Array.from({ length: 6 }, () => `
    <article class="token-card skeleton-card">
      <div class="skeleton title"></div><div class="skeleton line"></div>
      <div class="skeleton metric"></div><div class="skeleton line short"></div>
    </article>`).join("");
}

function sortedTokens() {
  const query = elements.search.value.trim().toLowerCase();
  const filtered = state.tokens.filter((token) =>
    [token.name, token.symbol, token.token_address].some((item) => String(item || "").toLowerCase().includes(query))
  );
  const value = (token, key) => Number(token[key]) || 0;
  const sorters = {
    trending: (a, b) => value(b, "volume_h1") - value(a, "volume_h1"),
    newest: (a, b) => new Date(b.pair_created_at || 0) - new Date(a.pair_created_at || 0),
    liquidity: (a, b) => value(b, "liquidity_usd") - value(a, "liquidity_usd"),
    volume: (a, b) => value(b, "volume_h24") - value(a, "volume_h24"),
    change: (a, b) => value(b, "price_change_h24") - value(a, "price_change_h24"),
  };
  return filtered.sort(sorters[elements.sort.value] || sorters.trending);
}

function card(token) {
  const address = escapeHtml(token.token_address);
  const dexUrl = safeUrl(token.dex_url);
  const bscscanUrl = token.token_address ? `https://bscscan.com/token/${encodeURIComponent(token.token_address)}` : "";
  const image = safeUrl(token.image_url);
  const social = (token.socials || []).map((item) => ({
    type: String(item.type || "").toLowerCase(),
    url: safeUrl(item.url),
  })).find((item) => item.url);
  const buys = token.txns_m5?.buys || 0;
  const sells = token.txns_m5?.sells || 0;
  const logo = image
    ? `<img class="token-logo" src="${escapeHtml(image)}" alt="" loading="lazy" referrerpolicy="no-referrer" />`
    : `<span class="token-logo token-logo-fallback">${escapeHtml((token.symbol || "?").slice(0, 1))}</span>`;
  return `
    <article class="token-card">
      <div class="card-head">
        <div class="token-identity">${logo}<div class="token-title"><strong>${escapeHtml(token.symbol)}</strong><span>${escapeHtml(token.name)} · ${escapeHtml(token.dex_id)}</span></div></div>
        <span class="age-badge">${age(token.pair_created_at)}</span>
      </div>
      <div class="contract-row"><span title="${address}">${escapeHtml(shortAddress(token.token_address))}</span><button class="copy-button" type="button" data-copy="${address}" aria-label="复制合约地址"><svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3"/></svg></button></div>
      <div class="metrics">
        <div><span class="metric-label">市值 / 流动性</span><span class="metric-value">${money(token.market_cap_usd)} / ${money(token.liquidity_usd)}</span><span class="metric-sub">${escapeHtml(token.quote_symbol)} 主池</span></div>
        <div><span class="metric-label">5 分钟成交</span><span class="metric-value">${money(token.volume_m5)}</span><span class="metric-sub">买 ${buys} / 卖 ${sells}</span></div>
        <div><span class="metric-label">24 小时成交量</span><span class="metric-value">${money(token.volume_h24)}</span><span class="metric-sub">1h ${money(token.volume_h1)}</span></div>
        <div><span class="metric-label">24 小时涨跌</span>${change(token.price_change_h24)}<span class="metric-sub">5m ${token.price_change_m5 == null ? "—" : `${number.format(token.price_change_m5)}%`}</span></div>
      </div>
      <div class="safety-row"><span>审计待接入</span><span>聪明钱待接入</span></div>
      <div class="card-foot"><span class="data-source">实时价格 ${money(token.price_usd)}</span><div class="card-actions">${social ? `<a class="card-link" href="${escapeHtml(social.url)}" target="_blank" rel="noopener noreferrer">${social.type === "twitter" ? "X" : "官网"}</a>` : ""}${dexUrl ? `<a class="card-link" href="${escapeHtml(dexUrl)}" target="_blank" rel="noopener noreferrer">行情</a>` : ""}${bscscanUrl ? `<a class="card-link" href="${bscscanUrl}" target="_blank" rel="noopener noreferrer">合约</a>` : ""}</div></div>
    </article>`;
}

function render() {
  const tokens = sortedTokens();
  elements.grid.innerHTML = tokens.map(card).join("");
  elements.grid.hidden = tokens.length === 0;
  elements.empty.hidden = tokens.length !== 0;
  elements.count.textContent = `发现 ${state.tokens.length} 个市场标的 · 当前显示 ${tokens.length} 个`;
}

async function refresh() {
  if (state.loading) return;
  state.loading = true;
  elements.grid.setAttribute("aria-busy", "true");
  elements.refresh.classList.add("loading");
  elements.refresh.disabled = true;
  elements.banner.textContent = "正在同步 BSC 市场池与成交数据…";
  if (!state.tokens.length) skeletons();
  try {
    const query = new URLSearchParams({ limit: "30" });
    if (demoMode) query.set("demo", "true");
    const response = await fetch(`/api/bsc/tokens?${query}`, { headers: { Accept: "application/json" } });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    const payload = await response.json();
    state.tokens = Array.isArray(payload.tokens) ? payload.tokens : [];
    state.updatedAt = new Date(payload.updated_at);
    render();
    elements.connection.classList.remove("error");
    elements.connection.innerHTML = `<span></span>${payload.demo ? "演示模式" : "数据源在线"}`;
    elements.banner.textContent = payload.demo
      ? "当前为显式演示模式：所有代币与行情均为界面预览数据，不可用于交易判断。"
      : (state.tokens.length ? "最新资料样本已同步；卡片行情为实时公开市场数据，不代表全 BSC 热榜。" : "数据源在线，但最新资料样本中没有可展示的 BSC 标的。");
    elements.updated.textContent = `更新于 ${state.updatedAt.toLocaleTimeString("zh-CN", { hour12: false })}`;
    log(`${payload.demo ? "演示数据已载入" : "扫描完成"}：获取 ${state.tokens.length} 个 BSC 最新资料标的`);
  } catch (error) {
    if (!state.tokens.length) {
      elements.grid.innerHTML = "";
      elements.grid.hidden = true;
      elements.empty.hidden = false;
      elements.empty.querySelector("strong").textContent = "BSC 市场数据暂时不可用";
      elements.empty.querySelector("span").textContent = "请检查网络连接，稍后点击刷新重试。";
    }
    elements.connection.classList.add("error");
    elements.connection.innerHTML = "<span></span>连接异常";
    elements.banner.textContent = `同步失败：${error.message}`;
    log(`同步失败：${error.message}`, "error");
  } finally {
    state.loading = false;
    elements.grid.setAttribute("aria-busy", "false");
    elements.refresh.classList.remove("loading");
    elements.refresh.disabled = false;
  }
}

function configureTimer() {
  window.clearInterval(state.timer);
  state.timer = elements.auto.checked ? window.setInterval(refresh, 30_000) : null;
  log(elements.auto.checked ? "自动刷新已开启（30 秒）" : "自动刷新已暂停");
}

elements.search.addEventListener("input", render);
elements.sort.addEventListener("change", render);
elements.refresh.addEventListener("click", refresh);
elements.auto.addEventListener("change", configureTimer);
elements.grid.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  try {
    await navigator.clipboard.writeText(button.dataset.copy);
    const original = button.getAttribute("aria-label");
    button.setAttribute("aria-label", "已复制");
    button.title = "已复制";
    window.setTimeout(() => { button.setAttribute("aria-label", original); button.title = ""; }, 1200);
  } catch { log("浏览器阻止了剪贴板访问", "error"); }
});

document.querySelector("#logToggle").addEventListener("click", (event) => {
  const button = event.currentTarget;
  const expanded = button.getAttribute("aria-expanded") === "true";
  button.setAttribute("aria-expanded", String(!expanded));
  document.querySelector("#logBody").hidden = expanded;
});

document.querySelector("#themeButton").addEventListener("click", () => {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === "slate" ? "" : "slate";
});

configureTimer();
refresh();
