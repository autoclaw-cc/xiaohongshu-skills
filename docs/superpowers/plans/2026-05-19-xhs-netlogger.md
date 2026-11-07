# XHS NetLogger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Note on testing:** Per the spec (`docs/superpowers/specs/2026-05-19-xhs-netlogger-design.md`), this feature does NOT have automated tests — browser extension + live XHS domain cannot be reproduced offline. Each task uses manual smoke-verification steps instead of TDD. Run each verification BEFORE marking step complete.

**Goal:** 给小红书 Chrome 扩展加 netlogger 能力（彩蛋激活），抓取 webRequest 全量 + fetch/XHR 响应体，分类展示用于反推 XHS 检测维度。

**Architecture:** 方案 B —— `chrome.webRequest` 4 阶段监听拿外层 HTTP 信号（含跨域风控上报域请求体），`interceptor.js` MAIN world hook fetch/XHR 拿业务域响应体；两者按 `(method, url, 2s 时间窗)` 关联合并；存 `chrome.storage.local`；popup 内"NetLog"卡片（标题连点 5 次激活）展示时序流 + 检测维度归类双 tab。

**Tech Stack:** Chrome Extension MV3、`chrome.webRequest`/`chrome.storage.local`/`chrome.runtime.sendMessage`/`chrome.scripting`、原生 JS（无构建工具）。

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `extension/netlogger.js` | Create | webRequest 监听 + 环形缓冲 + storage 持久化 + interceptor 信号关联 + 分类逻辑 |
| `extension/background.js` | Modify | importScripts netlogger.js；新增 NetLog 相关 runtime.onMessage 路由；转发 interceptor 信号 |
| `extension/content.js` | Modify | 转发 interceptor 上来的 NETLOG_INTERCEPTOR_ENTRY 消息到 background |
| `extension/interceptor.js` | Modify | 启用 netlog 时记录全量 fetch/XHR 请求/响应体，postMessage 上报 |
| `extension/popup.html` | Modify | 加 NetLog 卡片（默认隐藏）+ tab 切换样式 + 自适应宽度 |
| `extension/popup.js` | Modify | 标题彩蛋点击计数；NetLog 面板渲染（时序流 / 检测维度 / 详情展开 / 导出） |
| `extension/manifest.json` | Modify | host_permissions 加风控上报域（先宽松调研，后固化） |

---

## Task 1: 调研风控上报域名 + 临时宽松 host_permissions

**目的：** 我们不知道 XHS 实际用哪些跨域上报指纹/风控数据。先开宽松 host_permissions 监听一阵，看 chrome devtools network 抓到哪些跨域 host，再固化到 manifest。

**Files:**
- Modify: `extension/manifest.json`
- Doc update: `docs/superpowers/specs/2026-05-19-xhs-netlogger-design.md` (回填调研结论)

- [ ] **Step 1.1：在 manifest.json 加宽松调研域**

修改 `extension/manifest.json` 的 `host_permissions`，临时允许所有 https，方便监听：

```json
"host_permissions": [
  "https://www.xiaohongshu.com/*",
  "https://xiaohongshu.com/*",
  "https://creator.xiaohongshu.com/*",
  "ws://localhost/*",
  "https://*/*"
]
```

- [ ] **Step 1.2：在 chrome://extensions 重新加载扩展**

打开 `chrome://extensions/`，找到 "XHS Bridge"，点 reload 按钮。确认无错误（manifest 解析不通过会显示红色错误）。

- [ ] **Step 1.3：打开 XHS 并采集跨域请求列表**

- 打开 chrome devtools Network 面板，勾选 Preserve log
- 访问 `https://www.xiaohongshu.com/`，登录账号
- 浏览首页 30 秒（滚动几屏）
- 搜索一个关键词，进入一个笔记详情
- 在 Network 筛选器输入 `-domain:*.xiaohongshu.com`（过滤掉 XHS 自家域）
- 把所有剩余请求的 host 记录到剪贴板（按 host 分组、去重）

预期看到的候选：`fp.xiaohongshu.com` / `fp.snssdk.com` / `aegis.alicdn.com` / `*.bytedance.com` / `sentry.io` / `*.googleapis.com`（字体等可忽略）。

- [ ] **Step 1.4：固化 host_permissions**

把 Step 1.3 抓到的真实风控/上报域写入 `extension/manifest.json`（保留具体子域，不再用 `https://*/*` 宽松通配），例如：

```json
"host_permissions": [
  "https://www.xiaohongshu.com/*",
  "https://xiaohongshu.com/*",
  "https://creator.xiaohongshu.com/*",
  "https://fp.xiaohongshu.com/*",
  "https://sec.xiaohongshu.com/*",
  "ws://localhost/*"
]
```

（具体最终清单按 1.3 调研结果决定。）

- [ ] **Step 1.5：回填调研结论到 spec**

在 `docs/superpowers/specs/2026-05-19-xhs-netlogger-design.md` 文件末尾追加 "## 附录：风控上报域名调研（2026-05-19）" 章节，列出 1.3 抓到的所有跨域 host + 一句说明（来源/可能用途）。

- [ ] **Step 1.6：commit**

```bash
git add extension/manifest.json docs/superpowers/specs/2026-05-19-xhs-netlogger-design.md
git commit -m "feat(extension): manifest 添加 XHS 风控上报域 host_permissions

调研后固化的真实跨域上报 host 清单，详见 spec 附录。"
```

---

## Task 2: 创建 netlogger.js 骨架（开关 + 环形缓冲 + storage）

**目的：** 先把数据结构和状态管理建好，不接 webRequest（下一个 task 才接）。

**Files:**
- Create: `extension/netlogger.js`
- Modify: `extension/background.js` (顶部加 importScripts；onMessage 加 NetLog 路由)

- [ ] **Step 2.1：创建 netlogger.js 骨架**

新文件 `extension/netlogger.js`：

```javascript
/**
 * XHS NetLogger - 全量请求监听 + 检测维度归类
 *
 * 仅在 chrome.storage.local.netlogEnabled === true 时记录。
 * 环形缓冲 500 条；每 10 条 / 关键事件触发写入 storage。
 * 详细设计：docs/superpowers/specs/2026-05-19-xhs-netlogger-design.md
 */

const NETLOG_MAX_ENTRIES   = 500;
const NETLOG_REQBODY_MAX   = 2048;
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
```

- [ ] **Step 2.2：background.js 顶部 importScripts**

修改 `extension/background.js` 顶部（在 `const BRIDGE_URL = ...` 之前），加：

```javascript
importScripts("netlogger.js");
netlogInit();
```

- [ ] **Step 2.3：onMessage 路由加 NetLog 接口**

在 `extension/background.js` 的 `chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => { ... })` 内，在已有 case 之后（`GET_404_DIAGNOSTICS` 之后、闭合 `})` 之前）插入：

```javascript
  if (msg.type === "NETLOG_GET_ALL") {
    sendResponse({ entries: netlogGetAll(), enabled: netlogIsEnabled() });
    return true;
  }
  if (msg.type === "NETLOG_CLEAR") {
    netlogClear();
    sendResponse({ ok: true });
    return true;
  }
  if (msg.type === "NETLOG_SET_ENABLED") {
    netlogSetEnabled(msg.enabled);
    sendResponse({ ok: true, enabled: netlogIsEnabled() });
    return true;
  }
  if (msg.type === "NETLOG_GET_ENABLED") {
    sendResponse({ enabled: netlogIsEnabled() });
    return true;
  }
```

- [ ] **Step 2.4：手工验证消息接口**

reload 扩展。打开扩展 service worker 的 devtools（chrome://extensions → XHS Bridge → "service worker"），在 console 执行：

```javascript
chrome.runtime.sendMessage({ type: "NETLOG_GET_ENABLED" }, console.log);
// 期望：{ enabled: false }
chrome.runtime.sendMessage({ type: "NETLOG_SET_ENABLED", enabled: true }, console.log);
// 期望：{ ok: true, enabled: true }
chrome.runtime.sendMessage({ type: "NETLOG_GET_ALL" }, console.log);
// 期望：{ entries: [], enabled: true }
chrome.runtime.sendMessage({ type: "NETLOG_SET_ENABLED", enabled: false }, console.log);
// 期望：{ ok: true, enabled: false }
```

如有任何一条返回 `undefined` 或报错，回到 Step 2.1-2.3 检查。

- [ ] **Step 2.5：commit**

```bash
git add extension/netlogger.js extension/background.js
git commit -m "feat(extension): netlogger 骨架（启用开关 + 环形缓冲 + storage）"
```

---

## Task 3: webRequest 4 阶段监听 → 写入缓冲

**目的：** 接通 webRequest 抓所有请求。这是 netlogger 的核心数据源。

**Files:**
- Modify: `extension/netlogger.js`

- [ ] **Step 3.1：定义请求/响应 header 白名单 + cookie 提取工具**

在 `extension/netlogger.js` 文件底部追加：

```javascript
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

const NETLOG_COOKIE_KEYS = ["a1", "web_session", "webId", "gid"];

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
```

- [ ] **Step 3.2：实现 onBeforeRequest（拿 reqBody bytes）**

在 `extension/netlogger.js` 底部追加：

```javascript
const NETLOG_URL_FILTER = {
  urls: ["<all_urls>"],   // host_permissions 实际限制范围，这里全开让 webRequest 走 host_permissions 过滤
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
```

- [ ] **Step 3.3：实现 onSendHeaders（拿请求头 + cookie 指纹）**

在 `extension/netlogger.js` 底部追加：

```javascript
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
```

- [ ] **Step 3.4：实现 onHeadersReceived（拿响应头 / 状态 / set-cookie）**

在 `extension/netlogger.js` 底部追加：

```javascript
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
```

- [ ] **Step 3.5：实现 onCompleted + onErrorOccurred（finalize 入栈）**

在 `extension/netlogger.js` 底部追加：

```javascript
function _netlogFinalize(details, isError) {
  if (!_netEnabled) return;
  const entry = _netPending.get(details.requestId);
  _netPending.delete(details.requestId);
  if (!entry) return;
  entry.duration_ms = Date.now() - entry._t0;
  delete entry._t0;
  if (isError) entry.err = details.error || "network_error";
  // 分类逻辑由后续 task 5 接入
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
```

- [ ] **Step 3.6：手工验证 webRequest 抓到了请求**

reload 扩展。在 service worker devtools console 执行：

```javascript
chrome.runtime.sendMessage({ type: "NETLOG_SET_ENABLED", enabled: true });
chrome.runtime.sendMessage({ type: "NETLOG_CLEAR" });
```

打开 https://www.xiaohongshu.com/，浏览 10 秒。再回 SW console 执行：

```javascript
chrome.runtime.sendMessage({ type: "NETLOG_GET_ALL" }, r => console.log(r.entries.length, r.entries.slice(0, 3)));
```

预期：`entries.length > 0`，输出 3 条样本，包含 method/url/status/reqFingerprint.cookie/respHeaders。如 entries 是空，回 Step 3.2-3.5 检查。

- [ ] **Step 3.7：commit**

```bash
git add extension/netlogger.js
git commit -m "feat(extension): netlogger 接入 webRequest 4 阶段监听"
```

---

## Task 4: interceptor.js 全量 hook + 响应体上报

**目的：** webRequest 拿不到响应体，业务域内通过 fetch/XHR hook 补齐。

**Files:**
- Modify: `extension/interceptor.js`
- Modify: `extension/content.js`
- Modify: `extension/background.js`
- Modify: `extension/netlogger.js` (接收 interceptor 信号 + 关联)

- [ ] **Step 4.1：interceptor.js 加 netlog 启用状态同步**

修改 `extension/interceptor.js` —— 在文件顶部 IIFE 内、`const BLOCKED = new Set([404, 461, 403, 999]);` 后面，添加：

```javascript
  let _netlogEnabled = false;
  const RESP_BODY_MAX = 4096;

  // 通过 storage 同步 netlog 启用状态（MAIN world 不能直接读 chrome.storage）
  // content.js 监听 storage.local 变化，postMessage 给我们
  window.addEventListener("message", (e) => {
    if (e.data?.source === "xhs-netlog-status") {
      _netlogEnabled = !!e.data.enabled;
    }
  });
```

- [ ] **Step 4.2：interceptor.js 在 fetch hook 内增加全量上报**

修改 `extension/interceptor.js` 的 `window.fetch = async function (...) { ... }`，在 `const resp = await _fetch.call(this, input, init);` 之后插入响应体抓取逻辑：

```javascript
    const resp = await _fetch.call(this, input, init);

    // 现有 BLOCKED 诊断保持不变
    if (BLOCKED.has(resp.status) && (isApiUrl(url) || url.includes("xiaohongshu.com"))) {
      emit(buildEvent(url, method, resp.status, headers, { intercept_type: "fetch" }));
    }

    // NetLog 全量记录（仅启用时）
    if (_netlogEnabled && url.includes("xiaohongshu.com")) {
      let respBody = null;
      try {
        const clone = resp.clone();
        const text = await clone.text();
        respBody = text.length > RESP_BODY_MAX ? text.slice(0, RESP_BODY_MAX) + "…[cut]" : text;
      } catch (_) { respBody = "[unreadable]"; }

      window.postMessage({
        source: "xhs-netlog-intercept",
        method,
        url,
        status: resp.status,
        reqHeaders: headers,
        respBody,
        ts: Date.now(),
      }, "*");
    }

    return resp;
  };
```

- [ ] **Step 4.3：interceptor.js 在 XHR hook 内增加全量上报**

修改 `extension/interceptor.js` 的 `XMLHttpRequest.prototype.send = function () { ... }`，在 `loadend` 监听器内现有 BLOCKED 逻辑之后添加：

```javascript
  XMLHttpRequest.prototype.send = function () {
    this.addEventListener("loadend", () => {
      const url = this.__i_url || "";

      // 现有 BLOCKED 诊断保持不变
      if (BLOCKED.has(this.status) && (isApiUrl(url) || url.includes("xiaohongshu.com"))) {
        emit(buildEvent(url, this.__i_method || "GET", this.status, this.__i_headers || {}, {
          intercept_type: "xhr",
        }));
      }

      // NetLog 全量记录
      if (_netlogEnabled && url.includes("xiaohongshu.com")) {
        let respBody = null;
        try {
          const t = this.responseText || "";
          respBody = t.length > RESP_BODY_MAX ? t.slice(0, RESP_BODY_MAX) + "…[cut]" : t;
        } catch (_) { respBody = "[unreadable]"; }

        window.postMessage({
          source: "xhs-netlog-intercept",
          method: this.__i_method || "GET",
          url,
          status: this.status,
          reqHeaders: this.__i_headers || {},
          respBody,
          ts: Date.now(),
        }, "*");
      }
    });
    return _xhrSend.apply(this, arguments);
  };
```

- [ ] **Step 4.4：content.js 转发 interceptor 信号 + 同步启用状态**

修改 `extension/content.js`，在文件底部追加（或合并到已有 message 监听器内 — 看现有结构决定）：

```javascript
// ─── NetLog 信号转发 + 启用状态同步 ──────────────────────────────

// MAIN world interceptor → content (postMessage) → background (runtime)
window.addEventListener("message", (e) => {
  if (e.source !== window) return;
  if (e.data?.source === "xhs-netlog-intercept") {
    chrome.runtime.sendMessage({
      type: "NETLOG_INTERCEPTOR_ENTRY",
      payload: e.data,
    }).catch(() => {});
  }
});

// 启动时同步 netlog 启用状态到 MAIN world
function _syncNetlogStatus() {
  chrome.runtime.sendMessage({ type: "NETLOG_GET_ENABLED" }, (resp) => {
    window.postMessage({ source: "xhs-netlog-status", enabled: !!resp?.enabled }, "*");
  });
}
_syncNetlogStatus();

// 监听 background 推送的启用状态变化
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "NETLOG_ENABLED_CHANGED") {
    window.postMessage({ source: "xhs-netlog-status", enabled: !!msg.enabled }, "*");
  }
});
```

- [ ] **Step 4.5：background.js 接 interceptor 信号 + 广播状态变化**

修改 `extension/background.js` 的 `chrome.runtime.onMessage.addListener` 内，在 NetLog 路由块（Step 2.3 加的）后面添加：

```javascript
  if (msg.type === "NETLOG_INTERCEPTOR_ENTRY") {
    netlogIngestInterceptor(msg.payload);
    return false;
  }
```

并修改 `netlogSetEnabled` 调用点（找到 `NETLOG_SET_ENABLED` 路由），加广播：

```javascript
  if (msg.type === "NETLOG_SET_ENABLED") {
    netlogSetEnabled(msg.enabled);
    chrome.tabs.query({ url: ["*://*.xiaohongshu.com/*"] }, (tabs) => {
      for (const t of tabs) {
        chrome.tabs.sendMessage(t.id, { type: "NETLOG_ENABLED_CHANGED", enabled: msg.enabled }).catch(() => {});
      }
    });
    sendResponse({ ok: true, enabled: netlogIsEnabled() });
    return true;
  }
```

- [ ] **Step 4.6：netlogger.js 实现 ingestInterceptor（关联回填）**

在 `extension/netlogger.js` 底部追加：

```javascript
const NETLOG_INTERCEPTOR_WINDOW_MS = 2000;

function netlogIngestInterceptor(payload) {
  if (!_netEnabled || !payload) return;

  // 在最近 2s 内倒序找匹配 (method + url)，给该 entry 填响应体
  for (let i = _netBuffer.length - 1; i >= 0; i--) {
    const e = _netBuffer[i];
    if (payload.ts - e.ts > NETLOG_INTERCEPTOR_WINDOW_MS) break;
    if (e.method === payload.method && e.url === payload.url && !e.respBody) {
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
  _netlogPush({
    id: `${payload.ts}_intercept`,
    requestId: "",
    ts: payload.ts,
    tsLabel: _tsLabel(payload.ts),
    method: payload.method,
    url: payload.url,
    host: (() => { try { return new URL(payload.url).host; } catch (_) { return ""; } })(),
    path: (() => { try { const u = new URL(payload.url); return u.pathname + (u.search || ""); } catch (_) { return payload.url; } })(),
    resourceType: "xmlhttprequest",
    tabId: -1,
    reqHeaders: payload.reqHeaders || {},
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
  });
}
```

- [ ] **Step 4.7：手工验证响应体关联**

reload 扩展。SW console 启用：

```javascript
chrome.runtime.sendMessage({ type: "NETLOG_CLEAR" });
chrome.runtime.sendMessage({ type: "NETLOG_SET_ENABLED", enabled: true });
```

刷新 xiaohongshu.com 首页，浏览 10 秒。SW console 查：

```javascript
chrome.runtime.sendMessage({ type: "NETLOG_GET_ALL" }, r => {
  const withBody = r.entries.filter(e => e.respBody);
  console.log("总", r.entries.length, "有响应体", withBody.length, withBody[0]);
});
```

预期：xiaohongshu.com 域请求至少 30% 有 `respBody`（业务域 fetch/XHR 会被关联回填）。如全为 null，回 Step 4.1-4.6 检查；常见问题：interceptor world 没设 MAIN（manifest.json content_scripts[0].world）/ postMessage source 拼写不一致。

- [ ] **Step 4.8：commit**

```bash
git add extension/interceptor.js extension/content.js extension/background.js extension/netlogger.js
git commit -m "feat(extension): interceptor 全量 fetch/XHR hook 抓响应体，关联回填 netlog"
```

---

## Task 5: 检测维度分类 + cookieDiff

**目的：** 把入栈前的 entry 自动归类（category）和打信号（signals），为 popup 检测维度 tab 提供数据。

**Files:**
- Modify: `extension/netlogger.js`

- [ ] **Step 5.1：实现 classify + cookieDiff 函数**

在 `extension/netlogger.js` 文件底部追加：

```javascript
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
    // cookie_change 由 cookieDiff 进一步判断（见下）
    category = "other";
  } else if (entry.resourceType === "main_frame") {
    category = "page_nav";
  } else if (entry.path && entry.path.includes("/api/") && entry.status >= 200 && entry.status < 300) {
    category = "business_api";
  }

  return { category, signals };
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
```

- [ ] **Step 5.2：在 finalize 时调用 classify + cookieDiff**

修改 `extension/netlogger.js` 的 `_netlogFinalize` 函数（Step 3.5 写的），在 `_netlogPush(entry)` 之前插入分类逻辑：

```javascript
function _netlogFinalize(details, isError) {
  if (!_netEnabled) return;
  const entry = _netPending.get(details.requestId);
  _netPending.delete(details.requestId);
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
    entry.signals.push("set_cookie_changed:" + entry.cookieDiff.added.join(","));
  }

  _netlogPush(entry);
}
```

同样在 `netlogIngestInterceptor` 的 orphan 分支（Step 4.6）里，在 `_netlogPush(...)` 之前给 orphan entry 也跑一次分类：

修改 `extension/netlogger.js` 的 `netlogIngestInterceptor`，在构造 orphan entry 之后、`_netlogPush(...)` 之前插入：

```javascript
  // orphan 也跑分类
  const orphanEntry = /* 上面构造的对象 */;  // 提取到变量
  const { category, signals } = _netlogClassify(orphanEntry);
  orphanEntry.category = category;
  orphanEntry.signals = signals;
  _netlogPush(orphanEntry);
```

具体改法：把 Step 4.6 中 `_netlogPush({ ... })` 改成：

```javascript
  const orphanEntry = {
    id: `${payload.ts}_intercept`,
    requestId: "",
    /* ...原 Step 4.6 中所有字段 ... */
    _orphan: true,
  };
  const { category, signals } = _netlogClassify(orphanEntry);
  orphanEntry.category = category;
  orphanEntry.signals = signals;
  _netlogPush(orphanEntry);
```

- [ ] **Step 5.3：手工验证分类结果**

reload 扩展。SW console：

```javascript
chrome.runtime.sendMessage({ type: "NETLOG_CLEAR" });
chrome.runtime.sendMessage({ type: "NETLOG_SET_ENABLED", enabled: true });
```

打开 xiaohongshu.com，浏览 + 搜索 30 秒。再访问一个 fake 笔记 URL（去掉 xsec_token）：`https://www.xiaohongshu.com/explore/abc1234567890`。

SW console 查分类汇总：

```javascript
chrome.runtime.sendMessage({ type: "NETLOG_GET_ALL" }, r => {
  const groups = {};
  for (const e of r.entries) groups[e.category] = (groups[e.category] || 0) + 1;
  console.log("分类汇总", groups);
  console.log("signature_failure 样本:", r.entries.find(e => e.category === "signature_failure"));
});
```

预期：至少出现 `business_api / page_nav / risk_redirect` 或 `signature_failure`（手工触发的 fake URL 应当落入后两类之一）。

- [ ] **Step 5.4：commit**

```bash
git add extension/netlogger.js
git commit -m "feat(extension): netlogger 加入分类 + cookieDiff 逻辑"
```

---

## Task 6: popup UI - 彩蛋激活 + NetLog 卡片 + 时序流

**目的：** popup 标题连点 5 次激活 NetLog；激活后 popup 增宽，下方显示时序流。

**Files:**
- Modify: `extension/popup.html`
- Modify: `extension/popup.js`

- [ ] **Step 6.1：popup.html 添加 NetLog 卡片骨架（默认隐藏）+ 自适应宽度样式**

修改 `extension/popup.html`。

将 `body { width: 220px; ... }` 改为：

```css
body {
  width: 220px;
  padding: 16px;
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 13px;
  color: #333;
  margin: 0;
  transition: width 0.2s;
}
body.netlog-on { width: 580px; }
```

并在 `<style>` 末尾追加 NetLog 专属样式：

```css
.netlog-card { display: none; }
body.netlog-on .netlog-card { display: block; }
.netlog-tabs { display: flex; gap: 4px; margin-bottom: 6px; }
.netlog-tab {
  padding: 3px 10px;
  font-size: 11px;
  cursor: pointer;
  border-radius: 4px;
  background: #f1f3f4;
  color: #555;
}
.netlog-tab.active { background: #1a73e8; color: #fff; }
.netlog-row {
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-size: 10px;
  line-height: 1.5;
  padding: 2px 4px;
  border-radius: 3px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}
.netlog-row:hover { background: #f8f9fa; }
.netlog-row.cat-fingerprint_upload { color: #b7950b; }
.netlog-row.cat-business_error,
.netlog-row.cat-risk_redirect,
.netlog-row.cat-signature_failure { color: #c5221f; }
.netlog-row.cat-cookie_change { color: #1565c0; }
.netlog-list { max-height: 320px; overflow-y: auto; border: 1px solid #eee; padding: 4px; border-radius: 4px; }
.netlog-detail {
  background: #fafafa;
  border: 1px solid #eee;
  border-radius: 4px;
  padding: 6px;
  margin-top: 6px;
  font-family: ui-monospace, monospace;
  font-size: 10px;
  max-height: 200px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.netlog-toolbar { display: flex; gap: 6px; margin-bottom: 6px; }
.netlog-toolbar button {
  flex: 1;
  padding: 3px 0;
  font-size: 11px;
  border: 1px solid #ddd;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
}
```

在 `<body>` 内、`<script type="module" src="popup.js"></script>` 之前追加 NetLog 卡片 HTML：

```html
<hr class="divider">
<div class="netlog-card">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
    <span style="font-size:12px;font-weight:600;color:#333">NetLog</span>
    <span style="font-size:10px;color:#888" id="netlog-count">0 条</span>
  </div>
  <div class="netlog-tabs">
    <div class="netlog-tab active" data-tab="stream">时序流</div>
    <div class="netlog-tab" data-tab="category">检测维度</div>
  </div>
  <div class="netlog-toolbar">
    <button id="netlog-clear">清空</button>
    <button id="netlog-export">导出 JSON</button>
    <button id="netlog-disable">关闭</button>
  </div>
  <div class="netlog-list" id="netlog-list"></div>
  <div class="netlog-detail" id="netlog-detail" style="display:none"></div>
</div>
```

将原 `<h3>XHS Bridge</h3>` 改为可点击标题：

```html
<h3 id="title-hit" style="cursor:default;user-select:none">XHS Bridge</h3>
```

- [ ] **Step 6.2：popup.js 加彩蛋点击逻辑 + 启用状态读取**

在 `extension/popup.js` 文件底部追加：

```javascript
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
```

- [ ] **Step 6.3：popup.js 加时序流渲染 + 实时增量**

在 `extension/popup.js` 文件底部继续追加：

```javascript
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
```

- [ ] **Step 6.4：手工验证彩蛋激活 + 时序流**

reload 扩展。点击工具栏图标打开 popup。点击 "XHS Bridge" 标题 5 次（500ms 内连点）。预期：popup 立即扩宽到 580px；底部出现 NetLog 卡片；如果之前 task 步骤里有跑过 xhs 浏览，应有时序流条目。打开 xiaohongshu.com 让请求发起，popup 时序流应该实时增加。

如果点 5 次后没激活：在 popup devtools（右键 popup → Inspect）console 看 `_netlogHits` 计数有没有累加，常见问题：title-hit id 没生效 / click handler 没绑定。

- [ ] **Step 6.5：commit**

```bash
git add extension/popup.html extension/popup.js
git commit -m "feat(extension): popup 加彩蛋激活 + NetLog 时序流面板"
```

---

## Task 7: popup UI - 检测维度 tab + JSON 导出

**目的：** "检测维度" tab 按 category 聚合展示；"导出 JSON" 把缓冲全量导出为下载文件。

**Files:**
- Modify: `extension/popup.js`

- [ ] **Step 7.1：popup.js 添加按 category 分组渲染**

在 `extension/popup.js` 的 `renderNetlogStream` 函数之后追加：

```javascript
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
          return `<div class="netlog-row cat-${e.category}" data-cat="${cat}" data-idx="${i}">
            ${e.tsLabel}  ${e.method.padEnd(4)} ${e.status || "?"}  ${e.host.replace(/^www\./, "")}${path}${signal ? "  ["+signal+"]" : ""}
          </div>`;
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
```

- [ ] **Step 7.2：popup.js 添加 JSON 导出**

在 `extension/popup.js` 文件底部追加：

```javascript
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
```

- [ ] **Step 7.3：手工验证检测维度 + 导出**

reload 扩展。打开 popup（NetLog 已激活态会自动恢复）。打开 xhs 浏览 30 秒。
- 点击 "检测维度" tab → 预期看到几个折叠分组（fingerprint_upload / business_api / page_nav / 等），每个分组下有条目；点击条目 → 详情区显示 JSON。
- 点击 "导出 JSON" → 浏览器下载一个文件名形如 `xhs-netlog-2026-05-19T...json` 的文件，打开能看到所有 entries。

- [ ] **Step 7.4：commit**

```bash
git add extension/popup.js
git commit -m "feat(extension): popup NetLog 检测维度 tab + JSON 导出"
```

---

## Task 8: 端到端冒烟 + 调研结果回填

**目的：** 完整跑通 spec 列出的所有场景，证明 netlogger 能反映检测维度。

**Files:**
- Modify: `docs/superpowers/specs/2026-05-19-xhs-netlogger-design.md`（追加冒烟报告）

- [ ] **Step 8.1：浏览场景**

1. 关浏览器再开（验证扩展重启不丢启用状态）
2. 打开 popup，确认 NetLog 已激活（因 storage 持久）
3. 打开 xhs 首页，滚动 30 秒
4. 切到 "检测维度" tab，确认看到 `fingerprint_upload` 类目（如果没有，说明 host_permissions 没覆盖到实际上报域，回 Task 1 复查）

- [ ] **Step 8.2：故意触发 signature_failure**

清空 NetLog，在地址栏直接输入：`https://www.xiaohongshu.com/explore/65000000000000000000000a`（无 xsec_token 的伪笔记 URL）。预期 NetLog 时序流出现 302 重定向到 `/404?error_code=300033`（或 300031），category 标为 `signature_failure` 或 `risk_redirect`。

- [ ] **Step 8.3：账号切换触发 clear**（本期未实现，留作后续验证）

**注意：** spec 提到 "用户切换账号（a1 变化）→ 触发自动 clear"，但当前 plan 没有任务实现这个逻辑（避免本期 scope 膨胀）。在本步骤里跳过验证，把 "账号切换自动 clear" 作为已知 follow-up 列入 spec 附录的 "已知限制" 章节。

- [ ] **Step 8.4：性能观察**

不启用 NetLog vs 启用 NetLog，分别在 xhs 首页滚动 30s，打开 chrome://serviceworker-internals/ 看扩展 SW 的 CPU 占用（人工对比）。预期启用后偶发尖峰、稳态低（< 5%）。

- [ ] **Step 8.5：把冒烟结果写入 spec 附录**

在 `docs/superpowers/specs/2026-05-19-xhs-netlogger-design.md` 末尾追加：

```markdown
## 附录：实施后冒烟结果（2026-05-19）

- Task 8.1 浏览首页：时序流 ~80 条，检测维度 tab 分布合理（business_api/page_nav 占多数，fingerprint_upload N 条，cookie_change M 条）。
- Task 8.2 signature_failure：实测触发 error_code=____ ，分类正确。
- Task 8.3 账号切换自动 clear：本期未实现，列入已知限制。
- Task 8.4 性能：启用后 SW CPU 偶发 X%，稳态 Y%。

## 已知限制（截至 2026-05-19）

- 账号切换不自动 clear（用户需手动清空）
- 无 CLI 暴露（用户决定）
- 跨域风控上报域响应体看不到（chrome.webRequest 限制）
- 同一 url 短时间高并发请求时关联可能漏配（2s 时间窗 + url 完全匹配）
```

把 `____` / `X` / `Y` 等占位换成实际观测值。

- [ ] **Step 8.6：commit**

```bash
git add docs/superpowers/specs/2026-05-19-xhs-netlogger-design.md
git commit -m "docs: 回填 XHS netlogger 冒烟结果与已知限制"
```

---

## Self-Review

**Spec coverage check（spec 章节 → plan 任务）：**

| Spec 章节 | 对应 Task |
|---|---|
| 目标 / 非目标 | 整体 plan |
| 选定方案 B | Task 2-5（webRequest + interceptor 融合） |
| 组件结构 | 全部 Task |
| 数据流 | Task 3 + Task 4 |
| 启用开关 / 彩蛋激活 | Task 2 (开关) + Task 6 (彩蛋) |
| interceptor ↔ netlogger 关联 | Task 4 |
| 检测维度自动分类 | Task 5 |
| 性能与容量 | Task 3 (skip types) + Task 5 (分类) + Task 8 (性能验证) |
| NetLogEntry schema | Task 3 (基础) + Task 4 (响应体) + Task 5 (分类字段) |
| popup NetLog 面板草图 | Task 6 (HTML+时序流) + Task 7 (检测维度+导出) |
| 错误处理 & 边缘情况 | 分散在各 Task；账号切换自动 clear 显式标为已知限制 |
| 实现顺序与工作量 | 严格按 spec 的 8 个 task |
| 测试方案 | Task 8 |

**Placeholder scan：** 无 TBD / TODO；Step 8.5 的 `____` / `X` / `Y` 是执行时填入的占位符（合理，由实施者用实测值替换）。

**Type consistency：**
- `_netEnabled` / `_netBuffer` / `_netPending` / `_lastHostCookies` 在 Task 2 定义，Task 3-5 一致使用
- `NETLOG_STORAGE_KEY` / `NETLOG_ENABLED_KEY` 在 Task 2 定义，Task 4 中通过相同 message type 间接使用
- message type 名一致：`NETLOG_GET_ALL` / `NETLOG_SET_ENABLED` / `NETLOG_GET_ENABLED` / `NETLOG_CLEAR` / `NETLOG_ENTRY_ADDED` / `NETLOG_ENABLED_CHANGED` / `NETLOG_INTERCEPTOR_ENTRY`
- category 枚举值一致：`fingerprint_upload` / `business_error` / `risk_redirect` / `signature_failure` / `cookie_change` / `business_api` / `page_nav` / `other`

**已知偏离 TDD：** 见文档头部说明（spec 决定不写自动化测试）。每个 task 用手工验证步骤代替。
