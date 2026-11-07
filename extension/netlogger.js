/**
 * XHS NetLogger - 全量请求监听 + 检测维度归类
 *
 * 仅在 chrome.storage.local.netlogEnabled === true 时记录。
 * 环形缓冲 500 条；每 10 条 / 关键事件触发写入 storage。
 * 详细设计：docs/superpowers/specs/2026-05-19-xhs-netlogger-design.md
 */

const NETLOG_MAX_ENTRIES   = 500;
const NETLOG_REQBODY_MAX   = 8192;
const NETLOG_RESPBODY_MAX  = 4096;
const NETLOG_FLUSH_EVERY_N = 10;
const NETLOG_STORAGE_KEY   = "netLog";
const NETLOG_ENABLED_KEY   = "netlogEnabled";

const _netBuffer = [];
const _netPending = new Map();            // requestId → 半成品 entry（跨 webRequest 4 阶段）
let _netEnabled = false;
let _netFlushCounter = 0;
let _lastHostCookies = new Map();         // host → Set<cookie name> for cookieDiff

function netlogIsEnabled() { return _netEnabled; }

function netlogSetEnabled(v) {
  _netEnabled = !!v;
  chrome.storage.local.set({ [NETLOG_ENABLED_KEY]: _netEnabled });
  if (!_netEnabled) _netPending.clear();
}

async function netlogInit() {
  const data = await chrome.storage.local.get([NETLOG_ENABLED_KEY, NETLOG_STORAGE_KEY]);
  _netEnabled = !!data[NETLOG_ENABLED_KEY];
  if (Array.isArray(data[NETLOG_STORAGE_KEY])) {
    _netBuffer.push(...data[NETLOG_STORAGE_KEY].slice(-NETLOG_MAX_ENTRIES));
  }
}

function netlogGetAll() { return _netBuffer.slice(); }

function netlogClear() {
  _netBuffer.length = 0;
  _netPending.clear();
  _lastHostCookies.clear();
  chrome.storage.local.set({ [NETLOG_STORAGE_KEY]: [] });
}

function _netlogFlush(force = false) {
  _netFlushCounter++;
  if (!force && _netFlushCounter % NETLOG_FLUSH_EVERY_N !== 0) return;
  chrome.storage.local.set({ [NETLOG_STORAGE_KEY]: _netBuffer.slice(-NETLOG_MAX_ENTRIES) });
}

function _netlogPush(entry) {
  _netBuffer.push(entry);
  if (_netBuffer.length > NETLOG_MAX_ENTRIES) {
    _netBuffer.splice(0, _netBuffer.length - NETLOG_MAX_ENTRIES);
  }
  _netlogFlush(entry.category === "business_error" ||
               entry.category === "signature_failure" ||
               entry.category === "risk_redirect");
  // 通知 popup 增量
  chrome.runtime.sendMessage({ type: "NETLOG_ENTRY_ADDED", entry }).catch(() => {});
}

// ─── Step 3.1: header 白名单 + cookie 工具 ───────────────────────────────────

const NETLOG_REQ_HEADER_WHITELIST = new Set([
  "xs", "xt", "x-s-common", "x-t", "x-mns-platform",
  "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "sec-fetch-user",
  "referer", "origin", "user-agent",
  "content-type", "accept", "accept-language",
]);

const NETLOG_RESP_HEADER_WHITELIST = new Set([
  "location", "set-cookie", "cache-control", "x-request-id",
  "content-type", "server", "x-application-context",
]);

const NETLOG_SKIP_TYPES = new Set(["image", "font", "stylesheet", "media"]);

function _filterHeaders(rawHeaders, whitelist) {
  const out = {};
  if (!rawHeaders) return out;
  for (const h of rawHeaders) {
    const k = h.name.toLowerCase();
    if (whitelist.has(k)) {
      // set-cookie 可能多个值，累加
      if (k === "set-cookie" && out[k]) out[k] += "\n" + h.value;
      else out[k] = h.value;
    }
  }
  return out;
}

function _parseCookieHeader(cookieStr) {
  const map = {};
  if (!cookieStr) return map;
  for (const part of cookieStr.split(";")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    map[part.slice(0, i).trim()] = part.slice(i + 1).trim();
  }
  return map;
}

function _extractReqBody(requestBody) {
  if (!requestBody) return null;
  if (requestBody.raw && requestBody.raw[0] && requestBody.raw[0].bytes) {
    try {
      return new TextDecoder().decode(requestBody.raw[0].bytes).slice(0, NETLOG_REQBODY_MAX);
    } catch (_) {
      return "[binary " + requestBody.raw[0].bytes.byteLength + "B]";
    }
  }
  if (requestBody.formData) {
    try {
      return JSON.stringify(requestBody.formData).slice(0, NETLOG_REQBODY_MAX);
    } catch (_) { return "[formData]"; }
  }
  return null;
}

function _tsLabel(ts) {
  const d = new Date(ts);
  return String(d.getHours()).padStart(2, "0") + ":" +
         String(d.getMinutes()).padStart(2, "0") + ":" +
         String(d.getSeconds()).padStart(2, "0") + "." +
         String(d.getMilliseconds()).padStart(3, "0");
}

// ─── Step 3.2: onBeforeRequest（拿 reqBody bytes） ────────────────────────────

const NETLOG_URL_FILTER = {
  urls: ["<all_urls>"],
};

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (!_netEnabled) return;
    if (NETLOG_SKIP_TYPES.has(details.type)) return;

    let host = "";
    try { host = new URL(details.url).host; } catch (_) {}

    _netPending.set(details.requestId, {
      id: `${details.timeStamp}_${details.requestId}`,
      requestId: details.requestId,
      ts: details.timeStamp,
      tsLabel: _tsLabel(details.timeStamp),
      method: details.method,
      url: details.url,
      host,
      path: (() => { try { const u = new URL(details.url); return u.pathname + (u.search || ""); } catch (_) { return details.url; } })(),
      resourceType: details.type,
      tabId: details.tabId,
      reqHeaders: {},
      reqBody: _extractReqBody(details.requestBody),
      reqFingerprint: null,
      status: 0,
      statusLine: "",
      respHeaders: {},
      respBody: null,
      setCookie: null,
      duration_ms: 0,
      err: null,
      category: "other",
      signals: [],
      cookieDiff: null,
      redirectTo: null,
      errorCode: null,
      _t0: Date.now(),
    });
  },
  NETLOG_URL_FILTER,
  ["requestBody"],
);

// ─── Step 3.3: onSendHeaders（拿请求头 + cookie 指纹） ────────────────────────

chrome.webRequest.onSendHeaders.addListener(
  (details) => {
    if (!_netEnabled) return;
    const entry = _netPending.get(details.requestId);
    if (!entry) return;

    entry.reqHeaders = _filterHeaders(details.requestHeaders, NETLOG_REQ_HEADER_WHITELIST);

    const cookieStr = (details.requestHeaders || []).find(h => h.name.toLowerCase() === "cookie")?.value || "";
    const cookieMap = _parseCookieHeader(cookieStr);
    const ua = entry.reqHeaders["user-agent"] || "";
    entry.reqFingerprint = {
      has_xs:        !!entry.reqHeaders["xs"],
      has_xt:        !!entry.reqHeaders["xt"],
      has_xsCommon:  !!entry.reqHeaders["x-s-common"],
      sec_fetch_site: entry.reqHeaders["sec-fetch-site"] || null,
      sec_fetch_mode: entry.reqHeaders["sec-fetch-mode"] || null,
      referer:        entry.reqHeaders["referer"] || null,
      origin:         entry.reqHeaders["origin"] || null,
      ua_prefix:      ua.slice(0, 80),
      cookie: {
        has_a1:              "a1" in cookieMap,
        has_web_session:     "web_session" in cookieMap,
        has_webId:           "webId" in cookieMap,
        has_gid:             "gid" in cookieMap,
        a1_preview:          cookieMap["a1"]          ? cookieMap["a1"].slice(0, 12)          + "…" : null,
        web_session_preview: cookieMap["web_session"] ? cookieMap["web_session"].slice(0, 10) + "…" : null,
      },
    };
  },
  NETLOG_URL_FILTER,
  ["requestHeaders", "extraHeaders"],
);

// ─── Step 3.4: onHeadersReceived（拿响应头 / 状态 / set-cookie） ──────────────

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (!_netEnabled) return;
    const entry = _netPending.get(details.requestId);
    if (!entry) return;

    entry.status = details.statusCode;
    entry.statusLine = (details.statusLine || "").replace(/^HTTP\/[\d.]+\s*/, "");
    entry.respHeaders = _filterHeaders(details.responseHeaders, NETLOG_RESP_HEADER_WHITELIST);

    if (entry.respHeaders["set-cookie"]) {
      entry.setCookie = entry.respHeaders["set-cookie"]
        .split("\n")
        .map(s => {
          const i = s.indexOf("=");
          return i > 0 ? s.slice(0, i).trim() : s.trim();
        });
    }

    if (details.statusCode === 301 || details.statusCode === 302) {
      entry.redirectTo = entry.respHeaders["location"] || null;
      if (entry.redirectTo) {
        try {
          const loc = new URL(entry.redirectTo, details.url);
          entry.errorCode = loc.searchParams.get("error_code");
        } catch (_) {}
      }
    }
  },
  NETLOG_URL_FILTER,
  ["responseHeaders", "extraHeaders"],
);

// ─── Step 3.5: onCompleted + onErrorOccurred（finalize 入栈） ─────────────────

function _netlogFinalize(details, isError) {
  const entry = _netPending.get(details.requestId);
  _netPending.delete(details.requestId);
  if (!_netEnabled) return;
  if (!entry) return;
  entry.duration_ms = Date.now() - entry._t0;
  delete entry._t0;
  if (isError) entry.err = details.error || "network_error";

  // 分类 + cookieDiff
  const { category, signals } = _netlogClassify(entry);
  entry.category = category;
  entry.signals = signals;
  entry.cookieDiff = _netlogComputeCookieDiff(entry);
  if (entry.cookieDiff) {
    entry.category = entry.category === "other" ? "cookie_change" : entry.category;
    const diffParts = [];
    if (entry.cookieDiff.added.length) {
      diffParts.push("added:" + entry.cookieDiff.added.join(","));
    }
    if (entry.cookieDiff.removed.length) {
      diffParts.push("removed:" + entry.cookieDiff.removed.join(","));
    }
    entry.signals.push("set_cookie_changed:" + diffParts.join(";"));
  }

  _netlogPush(entry);
}

chrome.webRequest.onCompleted.addListener(
  (d) => _netlogFinalize(d, false),
  NETLOG_URL_FILTER,
);
chrome.webRequest.onErrorOccurred.addListener(
  (d) => _netlogFinalize(d, true),
  NETLOG_URL_FILTER,
);

// ─── Step 4.6: ingestInterceptor（关联回填） ─────────────────────────────────

const NETLOG_INTERCEPTOR_WINDOW_MS = 2000;

function netlogIngestInterceptor(payload) {
  if (!_netEnabled || !payload) return;

  // 在最近 2s 内倒序找匹配 (method + url)，给该 entry 填响应体
  for (let i = _netBuffer.length - 1; i >= 0; i--) {
    const e = _netBuffer[i];
    if (payload.ts - e.ts > NETLOG_INTERCEPTOR_WINDOW_MS) break;
    const methodOk = payload.method === "?" || e.method === payload.method;
    if (methodOk && _netlogUrlMatch(e.url, payload.url) && !e.respBody) {
      e.respBody = payload.respBody;
      // 合并 interceptor 头（webRequest 拿不到的应用层头）
      for (const [k, v] of Object.entries(payload.reqHeaders || {})) {
        const lk = k.toLowerCase();
        if (!e.reqHeaders[lk]) e.reqHeaders[lk] = v;
      }
      return;
    }
  }

  // 没关联上，独立存
  const orphanEntry = {
    id: `${payload.ts}_intercept`,
    requestId: "",
    ts: payload.ts,
    tsLabel: _tsLabel(payload.ts),
    method: payload.method === "?" ? "UNKNOWN" : payload.method,
    url: payload.url,
    host: (() => { try { return new URL(payload.url).host; } catch (_) { return ""; } })(),
    path: (() => { try { const u = new URL(payload.url); return u.pathname + (u.search || ""); } catch (_) { return payload.url; } })(),
    resourceType: "xmlhttprequest",
    tabId: -1,
    reqHeaders: {},
    reqBody: null,
    reqFingerprint: null,
    status: payload.status,
    statusLine: "",
    respHeaders: {},
    respBody: payload.respBody,
    setCookie: null,
    duration_ms: 0,
    err: null,
    category: "other",
    signals: [],
    cookieDiff: null,
    redirectTo: null,
    errorCode: null,
    _orphan: true,
  };
  // 过白名单（与 webRequest 路径保持一致）
  for (const [k, v] of Object.entries(payload.reqHeaders || {})) {
    const lk = k.toLowerCase();
    if (NETLOG_REQ_HEADER_WHITELIST.has(lk)) {
      orphanEntry.reqHeaders[lk] = v;
    }
  }
  // orphan 也跑分类
  const { category, signals } = _netlogClassify(orphanEntry);
  orphanEntry.category = category;
  orphanEntry.signals = signals;
  _netlogPush(orphanEntry);
}

// ─── Step 5.1: 分类 + cookieDiff 函数 ────────────────────────────────────────

const NETLOG_FP_HOST_KEYWORDS = ["fp", "sec", "aegis", "sentry", "track", "log"];
const NETLOG_FP_BODY_KEYWORDS = ["webdriver", "navigator", "screen", "timezone", "platform"];

function _netlogClassify(entry) {
  const signals = [];
  let category = "other";

  const hostLow = entry.host.toLowerCase();
  const isFpHost = NETLOG_FP_HOST_KEYWORDS.some(kw => hostLow.includes(kw));
  const bodyLow = (entry.reqBody || "").toLowerCase();
  const fpBodyHit = NETLOG_FP_BODY_KEYWORDS.find(kw => bodyLow.includes(kw));

  // 优先级：signature_failure > risk_redirect > business_error > fingerprint_upload > cookie_change > business_api > page_nav > other
  if (entry.errorCode && /^30003[123]$/.test(entry.errorCode)) {
    category = "signature_failure";
    signals.push(`error_code:${entry.errorCode}`);
  } else if (entry.redirectTo && /\/(404|login)(\/|\?|$)/.test(entry.redirectTo)) {
    category = "risk_redirect";
    signals.push(`redirect:${entry.redirectTo.slice(0, 60)}`);
  } else if ([401, 403, 461, 999].includes(entry.status)) {
    category = "business_error";
    signals.push(`status:${entry.status}`);
  } else if (isFpHost || fpBodyHit) {
    category = "fingerprint_upload";
    signals.push("fingerprint_upload");
    if (fpBodyHit) signals.push(`body_contains:${fpBodyHit}`);
  } else if (entry.setCookie && entry.setCookie.length > 0) {
    // 阻断：带 Set-Cookie 的请求不归 page_nav/business_api，留给 _netlogFinalize 中的 cookieDiff 二阶段升级为 cookie_change
    category = "other";
  } else if (entry.resourceType === "main_frame") {
    category = "page_nav";
  } else if (entry.path && entry.path.includes("/api/") && entry.status >= 200 && entry.status < 300) {
    category = "business_api";
  }

  return { category, signals };
}

function _netlogUrlMatch(webRequestUrl, interceptorUrl) {
  if (webRequestUrl === interceptorUrl) return true;
  // interceptor 可能拿到相对路径 (fetch("/api/foo")) 或绝对路径
  // webRequest 总是绝对路径
  if (interceptorUrl.startsWith("/")) {
    try {
      return new URL(webRequestUrl).pathname + new URL(webRequestUrl).search
             === interceptorUrl.split("#")[0];
    } catch (_) { return false; }
  }
  // 都是绝对路径但有 query 顺序差异
  try {
    const a = new URL(webRequestUrl);
    const b = new URL(interceptorUrl);
    return a.host === b.host && a.pathname === b.pathname;
  } catch (_) { return false; }
}

function _netlogComputeCookieDiff(entry) {
  if (!entry.setCookie || entry.setCookie.length === 0) return null;
  const prev = _lastHostCookies.get(entry.host) || new Set();
  const curr = new Set(entry.setCookie);
  const added = [...curr].filter(k => !prev.has(k));
  const removed = [...prev].filter(k => !curr.has(k));
  _lastHostCookies.set(entry.host, curr);
  if (added.length === 0 && removed.length === 0) return null;
  return { added, removed, changed: [] };
}
