function renderStatus(wsConnected) {
  set("bridge-status", "bridge-dot", "bridge-text", wsConnected, wsConnected ? "已连接" : "未连接");
  set("ext-status",   "ext-dot",   "ext-text",   true, "运行中");
  document.getElementById("hint").textContent = wsConnected
    ? "一切正常，可以运行 Python 脚本。"
    : "请先运行：python scripts/cli.py <命令>";
}

function set(badgeId, dotId, textId, ok, label) {
  const cls = ok ? "ok" : "err";
  document.getElementById(badgeId).className  = `badge ${cls}`;
  document.getElementById(dotId).className    = `dot ${cls}`;
  document.getElementById(textId).textContent = label;
}

// 初始化：拉取当前状态
try {
  chrome.runtime.sendMessage({ type: "GET_STATUS" }, (resp) => {
    if (chrome.runtime.lastError || !resp?.success) {
      renderStatus(false);
      return;
    }
    renderStatus(resp.status.wsConnected);
  });
} catch (e) {
  renderStatus(false);
}

// 实时监听状态变化（background 主动推送）
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "STATUS_CHANGED") {
    renderStatus(msg.status.wsConnected);
  }
});

// ── 风控扫描 ──────────────────────────────────────────────────

const RISK_LABELS = { safe: "安全", low: "低风险", medium: "中风险", high: "高风险" };

document.getElementById("scan-btn").addEventListener("click", async () => {
  const btn = document.getElementById("scan-btn");
  const resultEl = document.getElementById("risk-result");
  btn.disabled = true;
  btn.textContent = "扫描中...";
  resultEl.style.display = "none";

  try {
    const report = await chrome.runtime.sendMessage({ type: "ANALYZE_RISK_CONTROL" });
    if (!report || report.error) {
      showRiskError(report?.error || "扫描失败，请检查扩展连接状态");
      return;
    }
    renderRiskReport(report);
  } catch (e) {
    showRiskError(String(e.message || e));
  } finally {
    btn.disabled = false;
    btn.textContent = "重新扫描";
  }
});

function renderRiskReport(report) {
  const badge = document.getElementById("risk-level-badge");
  const level = report.risk_level || "safe";
  badge.textContent = RISK_LABELS[level] || level;
  badge.className = `risk-badge risk-${level}`;

  const list = document.getElementById("issue-list");
  list.innerHTML = "";
  if (!report.issues || report.issues.length === 0) {
    const li = document.createElement("li");
    li.textContent = "✓ 未发现风控特征";
    li.style.color = "#1e8e3e";
    list.appendChild(li);
  } else {
    for (const issue of report.issues) {
      const li = document.createElement("li");
      const icon = issue.level === "high" ? "✗" : issue.level === "medium" ? "!" : "·";
      li.textContent = `${icon} ${issue.msg}`;
      li.style.color = issue.level === "high" ? "#c5221f" : issue.level === "medium" ? "#b7950b" : "#666";
      list.appendChild(li);
    }
  }

  document.getElementById("risk-result").style.display = "block";
}

function showRiskError(msg) {
  const badge = document.getElementById("risk-level-badge");
  badge.textContent = "错误";
  badge.className = "risk-badge risk-medium";
  const list = document.getElementById("issue-list");
  list.innerHTML = `<li style="color:#c5221f">${msg}</li>`;
  document.getElementById("risk-result").style.display = "block";
}

// ── 404 诊断事件面板 ──────────────────────────────────────────

const CAUSE_COLORS = {
  token:             "#b7950b",
  signature:         "#1565c0",
  session:           "#6a1b9a",
  ip_block:          "#c5221f",
  account_block:     "#a50000",
  risk_control:      "#c5221f",
  content_unavailable: "#555",
};

function renderEvents(events) {
  const el = document.getElementById("event-list");
  const badge  = document.getElementById("intercept-badge");
  const dot    = document.getElementById("intercept-dot");
  const count  = document.getElementById("intercept-count");

  if (events.length === 0) {
    el.innerHTML = '<span style="color:#aaa">暂无拦截记录</span>';
    badge.className = "badge loading";
    dot.className   = "dot loading";
    count.textContent = "监听中";
    return;
  }

  badge.className = "badge err";
  dot.className   = "dot err";
  count.textContent = `${events.length} 条`;

  el.innerHTML = events.slice(0, 10).map(ev => {
    const color = CAUSE_COLORS[ev.diagnosis?.cause_category] || "#555";
    const time  = ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString("zh-CN") : "";
    const urlShort = ev.url.replace(/https?:\/\/[^/]+/, "").slice(0, 45);
    return `
      <div style="border-left:3px solid ${color};padding:3px 6px;margin-bottom:4px;background:#fafafa;border-radius:0 4px 4px 0">
        <div style="color:${color};font-weight:600">[${ev.status}] ${ev.diagnosis?.root_cause || "未知"}</div>
        <div style="color:#666;font-size:9.5px">${urlShort}</div>
        <div style="color:#999;font-size:9px">${time} · ${ev.intercept_type || "fetch"}</div>
      </div>`;
  }).join("");
}

// 初始加载历史事件
chrome.runtime.sendMessage({ type: "GET_404_DIAGNOSTICS" }, (resp) => {
  renderEvents(resp?.events || []);
});

// 实时监听新事件
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "BLOCK_EVENT_ADDED") {
    chrome.runtime.sendMessage({ type: "GET_404_DIAGNOSTICS" }, (resp) => {
      renderEvents(resp?.events || []);
    });
  }
});

// 清空按钮
document.getElementById("clear-btn").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "XHS_BLOCK_EVENT", event: null }).catch(() => {});
  // 直接通过 background command 清空
  chrome.storage.session.set({ blockEvents: [] }, () => renderEvents([]));
});

// ─── NetLog 彩蛋激活 + 状态 ──────────────────────────────────────

const NETLOG_HIT_TARGET = 5;
const NETLOG_HIT_RESET_MS = 500;
let _netlogHits = 0;
let _netlogHitTimer = null;

const titleEl = document.getElementById("title-hit");
titleEl?.addEventListener("click", () => {
  _netlogHits++;
  clearTimeout(_netlogHitTimer);
  _netlogHitTimer = setTimeout(() => { _netlogHits = 0; }, NETLOG_HIT_RESET_MS);
  if (_netlogHits >= NETLOG_HIT_TARGET) {
    _netlogHits = 0;
    titleEl.style.transition = "background 0.2s";
    titleEl.style.background = "#fef9e7";
    setTimeout(() => { titleEl.style.background = ""; }, 300);
    chrome.runtime.sendMessage({ type: "NETLOG_GET_ENABLED" }, (resp) => {
      if (resp?.enabled) {
        if (confirm("关闭 NetLog?")) toggleNetlog(false);
      } else {
        toggleNetlog(true);
      }
    });
  }
});

function toggleNetlog(enabled) {
  chrome.runtime.sendMessage({ type: "NETLOG_SET_ENABLED", enabled }, () => {
    applyNetlogUI(enabled);
    if (enabled) refreshNetlog();
  });
}

function applyNetlogUI(enabled) {
  document.body.classList.toggle("netlog-on", !!enabled);
}

// 初始化：根据当前启用状态决定显示
chrome.runtime.sendMessage({ type: "NETLOG_GET_ENABLED" }, (resp) => {
  if (resp?.enabled) {
    applyNetlogUI(true);
    refreshNetlog();
  }
});

document.getElementById("netlog-disable")?.addEventListener("click", () => toggleNetlog(false));
document.getElementById("netlog-clear")?.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "NETLOG_CLEAR" }, () => refreshNetlog());
});

// ─── NetLog 时序流渲染 ──────────────────────────────────────────

let _netlogEntries = [];
let _netlogTab = "stream";

function refreshNetlog() {
  chrome.runtime.sendMessage({ type: "NETLOG_GET_ALL" }, (resp) => {
    _netlogEntries = resp?.entries || [];
    renderNetlog();
  });
}

function renderNetlog() {
  const countEl = document.getElementById("netlog-count");
  if (countEl) countEl.textContent = `${_netlogEntries.length} 条`;

  const list = document.getElementById("netlog-list");
  if (!list) return;

  if (_netlogTab === "stream") {
    renderNetlogStream(list);
  } else {
    renderNetlogCategory(list);
  }
}

const NETLOG_CAT_LABEL = {
  fingerprint_upload: "指纹↑",
  business_error: "错误",
  risk_redirect: "风控跳",
  signature_failure: "签名失败",
  cookie_change: "Cookie 变",
  business_api: "API",
  page_nav: "导航",
  other: "其他",
};

function renderNetlogStream(container) {
  // 最新在底，倒序展示前 200 条
  const slice = _netlogEntries.slice(-200);
  container.innerHTML = slice.map((e, i) => {
    const star = (e.category === "fingerprint_upload" || e.category === "risk_redirect" ||
                  e.category === "signature_failure") ? " ★" : "";
    const path = e.path.length > 50 ? e.path.slice(0, 47) + "…" : e.path;
    const host = e.host.replace(/^www\./, "");
    return `<div class="netlog-row cat-${e.category}" data-idx="${i}">
      ${e.tsLabel}  ${e.method.padEnd(4)} ${e.status || "?"}  ${e.duration_ms}ms  ${host}${path}  [${NETLOG_CAT_LABEL[e.category]}]${star}
    </div>`;
  }).join("");

  // 点击展开详情
  container.querySelectorAll(".netlog-row").forEach(row => {
    row.addEventListener("click", () => {
      const idx = Number(row.dataset.idx);
      showNetlogDetail(slice[idx]);
    });
  });
  // 滚到底
  container.scrollTop = container.scrollHeight;
}

function showNetlogDetail(entry) {
  const el = document.getElementById("netlog-detail");
  if (!el) return;
  el.style.display = "block";
  el.textContent = JSON.stringify(entry, null, 2);
  el.onclick = () => { el.style.display = "none"; };
  el.title = "点击此处关闭详情";
}

function renderNetlogCategory(container) {
  const groups = {};
  for (const e of _netlogEntries) {
    if (!groups[e.category]) groups[e.category] = [];
    groups[e.category].push(e);
  }

  const order = ["fingerprint_upload", "signature_failure", "risk_redirect",
                 "business_error", "cookie_change", "business_api", "page_nav", "other"];

  const sections = [];
  for (const cat of order) {
    if (!groups[cat] || groups[cat].length === 0) continue;
    const label = NETLOG_CAT_LABEL[cat];
    const items = groups[cat].slice(-50);  // 每类最多展示 50 条
    sections.push(`
      <details open style="margin-bottom:6px">
        <summary style="cursor:pointer;font-size:11px;font-weight:600;color:#444">▾ ${label} (${groups[cat].length})</summary>
        ${items.map((e, i) => {
          const path = e.path.length > 60 ? e.path.slice(0, 57) + "…" : e.path;
          const signal = (e.signals || []).slice(0, 2).join(", ");
          const signalSuffix = signal ? "  [" + signal + "]" : "";
          const hostShort = e.host.replace(/^www\./, "");
          return `<div class="netlog-row cat-${e.category}" data-cat="${cat}" data-idx="${i}">` +
                 `${e.tsLabel}  ${e.method.padEnd(4)} ${e.status || "?"}  ` +
                 `${hostShort}${path}${signalSuffix}</div>`;
        }).join("")}
      </details>
    `);
  }

  container.innerHTML = sections.join("") || '<div style="color:#aaa;font-size:11px;padding:8px">暂无数据</div>';

  // 详情点击：data-cat + data-idx 反查
  container.querySelectorAll(".netlog-row").forEach(row => {
    row.addEventListener("click", () => {
      const cat = row.dataset.cat;
      const idx = Number(row.dataset.idx);
      const entry = groups[cat]?.slice(-50)[idx];
      if (entry) showNetlogDetail(entry);
    });
  });
}

// tab 切换
document.querySelectorAll(".netlog-tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".netlog-tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    _netlogTab = tab.dataset.tab;
    renderNetlog();
  });
});

// 实时增量
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "NETLOG_ENTRY_ADDED" && document.body.classList.contains("netlog-on")) {
    _netlogEntries.push(msg.entry);
    if (_netlogEntries.length > 500) _netlogEntries.splice(0, _netlogEntries.length - 500);
    renderNetlog();
  }
});

document.getElementById("netlog-export")?.addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(_netlogEntries, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `xhs-netlog-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
