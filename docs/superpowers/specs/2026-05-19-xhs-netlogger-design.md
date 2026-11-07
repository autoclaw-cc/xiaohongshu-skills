# XHS NetLogger 设计文档

**日期**：2026-05-19
**分支**：feat/extension-bridge
**目标**：在浏览器扩展中加入小红书网页版网络监听能力，反向推导 XHS 用于检测自动化的"维度"，让插件 / Claude 操作时能规避风控。

参考实现：`D:\OSS\BHP_Production\BHP\modules\net-logger.js`（BOSS 直聘网页版的 webRequest 全量监听器）。

---

## 目标与非目标

### 目标

- 在 xiaohongshu.com 业务域 + 风控上报域采集足够信息，识别 XHS 服务端用来判定自动化的信号（cookie / header / 请求体指纹 / 响应错误码 / Set-Cookie 变化）。
- 数据在扩展 popup 内消费，同时通过 CLI 命令 `get-netlog` / `risk-report` 暴露给 LLM/Claude，让自动化操作过程中能主动判断风控状态。
- 隐藏入口：默认对普通使用零感知，通过彩蛋（标题连点 5 次）激活。

### 非目标

- 不做跨会话 diff、两会话对比（方案 C，本期延后）。
- 不做风控域名的自定义配置 UI（本期 hardcode）。
- 不做自动化测试（浏览器扩展 + 真实 XHS 域难离线复现）。
- 不替换现有 404 诊断（interceptor.js 现有逻辑）或 302 观测（background.js webRequest 现有逻辑），netlogger 是新增的独立 listener。

---

## 选定方案

**方案 B：webRequest 全量 + fetch/XHR hook 融合。**

- `chrome.webRequest` 4 阶段监听拿外层 HTTP 信号 + 请求体（指纹上报的关键载荷）
- `interceptor.js` 在 xiaohongshu.com 域 MAIN world hook fetch/XHR 拿响应体 + 应用层 header
- 按 `(method, url, 2s 时间窗)` 模糊关联两侧记录，合并为一条
- popup 面板「时序流」 + 「检测维度」双 tab

排除方案 A（webRequest only，看不到响应体与签名细节）与方案 C（B + 会话对比，工作量太大且 ROI 不确定，先把 B 做扎实）。

---

## 组件结构

| 文件 | 状态 | 职责 |
|---|---|---|
| `extension/netlogger.js` | 新建 | webRequest 4 阶段监听 + 环形缓冲 + storage 持久化 |
| `extension/interceptor.js` | 扩充 | 现有 404 诊断保留；新增"启用 netlog 时"记录全量 fetch/XHR 响应体 + 应用层 header |
| `extension/background.js` | 扩充 | importScripts netlogger.js；接 interceptor 上来的响应体信号；按 url+ts 关联 |
| `extension/popup.html` / `popup.js` | 扩充 | NetLog 卡片（彩蛋激活后显示）+ 时序流 / 检测维度 双 tab |
| `extension/manifest.json` | 扩充 | host_permissions 加风控上报域 |

## 数据流

```
用户请求
  │
  ├─→ chrome.webRequest (netlogger.js, background)
  │     ① onBeforeRequest        → method/url/reqBody bytes
  │     ② onSendHeaders          → 最终请求头（含 cookie/xs/xt/sec-fetch-*)
  │     ③ onHeadersReceived      → status/respHeaders/set-cookie
  │     ④ onCompleted/Error      → 耗时、错误码
  │
  └─→ interceptor.js (MAIN world, 仅 xiaohongshu.com)
        fetch/XHR hook → 响应体 + 应用层 header
        postMessage → content.js → background → netlogger
        ↓ 按 (method+url+时间窗) 模糊关联

netlogger 环形缓冲 (500 条)
  │
  ├─→ 每 10 条 / 关键事件触发写入
  │   chrome.storage.local.netLog
  │
  └─→ popup 拉取 + chrome.runtime push 实时增量
```

---

## 关键设计点

### A. 启用开关 / 彩蛋激活

- `chrome.storage.local.netlogEnabled`（boolean）作为持久化开关
- popup 顶部标题 "XHS Bridge" 连点 5 次切换；500ms 内无点击则计数器重置
- 激活后下方多出一张 "NetLog" 卡片
- 未启用时 netlogger / interceptor 的全部监听器 early return，零开销
- 已激活时再连点 5 次触发确认对话框，确认后关闭

### B. interceptor ↔ netlogger 关联

- 业务域内 interceptor 拿到响应后，按 `(method, url, 最近 2s 内的 webRequest 记录)` 关联回填响应体
- 关联失败的 interceptor 记录独立存入，标记 `_orphan: true`
- 跨域上报（fp / sentry / aegis 等）只走 webRequest 路径，不要求关联响应体

### C. 检测维度自动分类（启发式）

| category | 判定条件 |
|---|---|
| `fingerprint_upload` | host 含 fp / sec / aegis / sentry / track 关键字 OR 请求体含 webdriver / navigator / screen / timezone 字段 |
| `business_error` | status ∈ {401, 403, 461, 999} OR (HTTP 200 + 前端渲染 404) |
| `risk_redirect` | 302 → /404 或 /login |
| `signature_failure` | 302 Location 含 error_code=300031 / 300032 / 300033 |
| `cookie_change` | Set-Cookie 中新键 / 值变更（与上一条同 host 记录比较） |
| `business_api` | 路径含 /api/ 且 status=2xx |
| `page_nav` | resourceType=main_frame |
| `other` | 其他 |

一条 entry 可命中多个 signal（写入 `signals: string[]`），但 `category` 只取主分类（按上表自上而下的优先级）。

### D. 性能与容量

- 环形缓冲 500 条上限
- 单条请求体截断 2KB / 响应体截断 4KB
- 静态资源（image / font / stylesheet）按 `webRequest details.type` 过滤，不入栈
- 启用状态下预估额外开销：~5-10% CPU 在请求峰值时
- chrome.storage.local 限额 5MB，本设计总量预估 < 3MB

---

## 数据 Schema

```typescript
interface NetLogEntry {
  // ── 标识 ──
  id: string;                  // `${ts}_${requestId}`
  requestId: string;           // webRequest requestId
  ts: number;                  // onBeforeRequest 时间戳 (ms)
  tsLabel: string;             // "HH:mm:ss.SSS"

  // ── HTTP 基础 ──
  method: string;
  url: string;
  path: string;                // pathname + 关键 query (xsec_token=…&xsec_source=…)
  host: string;
  resourceType: string;        // main_frame / xhr / fetch / sub_frame
  tabId: number;

  // ── 请求（webRequest + interceptor 融合）──
  reqHeaders: Record<string, string>;     // 仅保留关键白名单
  reqBody: string | null;                 // 截断 2KB；raw bytes 解码或 formData JSON
  reqFingerprint: {
    has_xs: boolean;
    has_xt: boolean;
    has_xsCommon: boolean;
    sec_fetch_site: string | null;
    sec_fetch_mode: string | null;
    referer: string | null;
    origin: string | null;
    ua_prefix: string;                     // user-agent 前 80 字符
    cookie: {
      has_a1: boolean;
      has_web_session: boolean;
      has_webId: boolean;
      has_gid: boolean;
      a1_preview: string | null;
      web_session_preview: string | null;
    };
  };

  // ── 响应 ──
  status: number;
  statusLine: string;          // "200 OK" / "302 Found" / …
  respHeaders: Record<string, string>;
  respBody: string | null;     // 截断 4KB；仅业务域（interceptor 能拿到）
  setCookie: string[] | null;  // 解析后的 cookie 名列表

  // ── 时序 ──
  duration_ms: number;
  err: string | null;

  // ── 分析层（netlogger 计算）──
  category: NetLogCategory;
  signals: string[];           // 例: ["fingerprint_upload", "set_cookie_changed:sec_xxx"]
  cookieDiff: {
    added: string[];
    changed: string[];
    removed: string[];
  } | null;
  redirectTo: string | null;   // 302 Location
  errorCode: string | null;    // 300031/300032/… 从 location query 解析

  _orphan?: true;              // interceptor 未关联到 webRequest 时标记
}

type NetLogCategory =
  | "fingerprint_upload"
  | "business_error"
  | "risk_redirect"
  | "signature_failure"
  | "cookie_change"
  | "business_api"
  | "page_nav"
  | "other";
```

### 关键 cookie 白名单

请求 cookie 字段中以下 key 会被记录到 `reqFingerprint.cookie`：

- `a1`（设备指纹种子）
- `web_session`（登录态）
- `webId`
- `gid`
- 其他不在白名单的 cookie 名记入 `signals` 但值不存

### 关键 header 白名单（reqHeaders）

```
xs, xt, x-s-common, x-t, x-mns-platform,
sec-fetch-site, sec-fetch-mode, sec-fetch-dest, sec-fetch-user,
referer, origin, user-agent,
content-type, accept, accept-language
```

### 响应 header 白名单（respHeaders）

```
location, set-cookie, cache-control, x-request-id,
content-type, server, x-application-context
```

---

## popup NetLog 面板（草图）

```
┌─ XHS Bridge ───────────────────────┐
│ [logo] XHS Bridge       ● connected│  ← 标题连点 5 次激活
├────────────────────────────────────┤
│ 已连接到 bridge server             │
│ 当前账号：xxx                       │
│ [打开小红书]  [退出登录]            │
├────────────────────────────────────┤
│ ▼ NetLog  [enabled] [clear] [⬇json]│  ← 激活后显示
│ ┌──────────────────────────────┐   │
│ │[ 时序流 ] [ 检测维度 ]        │   │
│ ├──────────────────────────────┤   │
│ │ 时序流：                       │   │
│ │ 18:42:11.245 GET /api/sns/web/v1/feed  200 142ms [API]            │
│ │ 18:42:11.512 POST fp.snssdk.com/v1/fp  200 88ms  [指纹↑] ★       │
│ │ 18:42:12.103 GET /explore/abc?xsec_…   302 41ms  [风控跳转] ★    │
│ │ 18:42:12.180 GET /404?error_code=…     200 120ms [API]            │
│ │ …                                                                  │
│ ├──────────────────────────────┤   │
│ │ 检测维度（点击展开详情）：     │   │
│ │ ▾ 指纹上报 (12)                │   │
│ │   fp.snssdk.com/v1/fp  ×8 [body 含 webdriver/screen/…]           │
│ │   sec.xiaohongshu.com  ×4                                         │
│ │ ▾ 签名失败 (3)                 │   │
│ │   error_code=300033  token 与 session/IP 不匹配                  │
│ │ ▾ Cookie 变化 (5)              │   │
│ │   sec_xxx 新增（响应来自 /api/.../init）→ 风控触发标记           │
│ │ ▾ 业务错误 (2) / 风控跳转 (1)  │   │
│ └──────────────────────────────┘   │
└────────────────────────────────────┘
```

- 行点击展开看完整 entry（折叠 JSON 视图）
- `★` 标记需要重点关注的（指纹上报 / 风控跳转）
- "⬇json" 按钮把当前缓冲全量导出为 JSON 文件下载

---

## 错误处理与边缘情况

| 场景 | 处理 |
|---|---|
| webRequest 拿不到 reqBody（POST + 二进制） | `reqBody: "[binary]"`，只记字节数 |
| interceptor 跨标签页污染 | content.js 校验 sender.tab.id 与 background 记录的 tabId 匹配 |
| popup 关闭再开时数据丢失 | 数据存在 chrome.storage.local，popup 重新拉即可 |
| storage 写满（5MB 限制） | 环形缓冲 500 条 + 单条体积上限保证总量 < 3MB |
| 用户切换账号（a1 变化） | 触发自动 clear，避免跨账号污染分析 |
| 风控域名扩展 | host_permissions hardcode；本期不做配置 UI |
| service worker 重启丢内存缓冲 | webRequest listener 在 SW 重启后重新注册；内存丢失但 storage 持久部分保留 |

---

## 实现顺序与工作量估算

| # | 任务 | 改动文件 | 估算 | 验收 |
|---|---|---|---|---|
| 1 | 加风控域名到 host_permissions + 调研真实上报域名清单 | `manifest.json` | ~30 LOC | 装载扩展无错；调研结论补充到本文档 |
| 2 | 创建 netlogger.js：4 阶段 webRequest 监听 + 环形缓冲 + storage 持久化 + 启用开关 | 新建 `extension/netlogger.js` (~300 LOC) | ~3h | 启用后能在 storage 里看到 entries；关闭后零监听器活跃 |
| 3 | background.js 引入 netlogger + 暴露查询/清空消息接口 | `background.js` (~50 LOC) | ~30min | popup 能通过 runtime.sendMessage 拉到 log |
| 4 | interceptor.js 扩充：启用 netlog 时记录全量 fetch/XHR 响应体 + postMessage 上报 | `interceptor.js` (~80 LOC) | ~1.5h | 业务域内能拿到响应体并关联到 netlogger entry |
| 5 | netlogger 关联 + 分类逻辑：interceptor 信号回填 + category/signals/cookieDiff 计算 | `netlogger.js` (~150 LOC) | ~2h | 每条 entry 有正确 category；cookieDiff 能算出 Set-Cookie 变化 |
| 6 | popup UI：彩蛋激活 + NetLog 卡片 + 时序流 tab | `popup.html / popup.js` (~250 LOC) | ~3h | 标题连点 5 次激活；时序流实时刷新；点击行展开详情 |
| 7 | popup UI：检测维度 tab + 折叠分组 + json 导出按钮 | `popup.html / popup.js` (~150 LOC) | ~2h | 5 个分类正确归类；导出 JSON 文件可下载 |
| 8 | 手工冒烟测试：浏览/搜索/故意触发风控/切账号 | — | ~1h | 全场景跑通；分类无误判 |

**估算总量**：约 1000 LOC + 约 13h 集中工作时间。

---

## 主要风险与未知点

1. **风控上报域名清单不确定** — 目前对 XHS 实际使用的指纹上报域名只有部分猜测（fp.snssdk.com 等字节系常见域，但 XHS 是否用同套不确定）。任务 1 包含调研：先用宽松 host_permissions 临时监听一阵，看实际有哪些跨域 POST，再固化清单。
2. **interceptor ↔ webRequest 关联失败率** — 时间窗匹配可能漏关联（特别是高并发短连接）。先用 2s 窗 + url 完全匹配，观察漏匹配率，必要时改用 `Performance.getEntriesByName` 拿浏览器侧 timing 辅助匹配。
3. **chrome.storage.local 跨会话恢复** — Manifest V3 SW 在用户关闭浏览器时会被销毁；storage 持久但内存缓冲来不及写入的 N 条会丢。可接受。
4. **xs / xt 签名内部值无法拿到** — 只能拿到 fetch init.headers 里显式设置的；XHS SDK 若在更深层（如 Service Worker 拦截后注入）注入 header，fetch hook 也拿不到，需 chrome.debugger（本期不做）。
5. **彩蛋激活的 UX** — 5 次点击前无任何视觉反馈，新装扩展用户完全不知道这功能存在 —— 这是设计目标，不算风险。

---

## 测试方案

1. **手工冒烟**：
   - 激活 NetLog → 打开 xiaohongshu.com → 浏览/搜索 → 检查时序流是否完整、检测维度分类是否合理
   - 故意不带 xsec_token 直接访问 /explore/xxx → 验证抓到 `signature_failure` / `risk_redirect` 分类
   - 切账号 → 验证自动 clear 触发
2. **回归**：现有 `cmd_diagnose_404` / `cmd_check_risk` 不受影响（netlogger 是独立 listener，不替换原有逻辑）
3. **性能**：开 / 不开 netlog 在 xhs 首页滚动 30s，对比 SW CPU 占用（人工观察 chrome://serviceworker-internals）
4. 不写自动化测试

---

## 交付物

- 修改后的 `extension/{netlogger.js, background.js, interceptor.js, popup.html, popup.js, manifest.json}`
- 本设计文档
- 后续 brainstorming 出来的 implementation plan（spec 批准后由 writing-plans 产出）

---

## 附录：风控/上报域名调研（2026-05-19）

通过 `performance.getEntriesByType('resource')` 在 xiaohongshu.com 上抓到的跨域 host 清单（首页 + 搜索 + 笔记详情 浏览 ~1min）：

| Host | 用途推断 | 是否监听 |
|---|---|---|
| `apm-fe.xiaohongshu.com` | 前端 APM 监控（性能/错误/风控数据上报） | ✓ |
| `as.xiaohongshu.com` | 应用统计 / 事件上报 | ✓ |
| `edith.xiaohongshu.com` | XHS 主业务 API（含 /api/sns/web/...） | ✓ |
| `t2.xiaohongshu.com` | tracking 上报 | ✓ |
| `picasso-static.xiaohongshu.com` | 静态资源（图片处理服务） | ✓（业务域内，顺便监听） |
| `www.xiaohongshu.com` / `xiaohongshu.com` / `creator.xiaohongshu.com` | 业务页面 + API | ✓ |
| `sns-avatar-qc.xhscdn.com` / `sns-na-i2.xhscdn.com` / `sns-webpic-qc.xhscdn.com` | 图片 CDN | ✗（静态资源，过滤掉） |

**固化的 `host_permissions`：**

```json
"host_permissions": [
  "https://*.xiaohongshu.com/*",
  "https://xiaohongshu.com/*",
  "ws://localhost/*"
]
```

一行通配 `*.xiaohongshu.com` 覆盖所有当前 + 未来 XHS 子域。`xhscdn.com` 不加（图片资源 / NETLOG_SKIP_TYPES 已过滤）。

**调研未发现的潜在域名：**

- 没看到 `fp.xiaohongshu.com`（指纹）—— 可能用 a1 cookie 派生 + edith 内嵌而非独立子域
- 没看到 `sec.xiaohongshu.com`（安全）—— 同上
- 没看到 sentry / aegis 等第三方 —— XHS 用自家 apm-fe，未外接

如果后续遇到 netlog 漏抓某些请求，回头检查 host_permissions 是否需要扩展。

---

## CLI 风控接口

**背景**：Task 8 冒烟实测发现 XHS 检测维度的核心规律后，决定将 netlog 数据通过 CLI 暴露给 LLM/Claude，让自动化操作过程中能主动读取风控结论，替代此前"只在 popup 内消费"的决定。

### 命令

#### `get-netlog [--limit N]`

从扩展获取当前会话的 NetLog 原始 entries（最多 500 条环形缓冲）。

```bash
python scripts/cli.py get-netlog
python scripts/cli.py get-netlog --limit 50
```

输出格式：

```json
{
  "total": 123,
  "entries": [ /* NetLogEntry[] */ ]
}
```

未启用 netlogger 时：exit code 2 + 中文提示用户去 popup 彩蛋激活。

#### `risk-report`

调用 `scripts/xhs/risk_analyzer.py` 的 `analyze()` 函数，基于 netlog entries 反推检测维度并给出结构化风控结论。

```bash
python scripts/cli.py risk-report
```

输出格式：

```json
{
  "risk_level": "safe | low | medium | high | unknown",
  "total_requests": 123,
  "summary": "本会话采集 123 条请求，指纹上报 2 次，行为埋点 54 次。",
  "detection_axes": {
    "browser_fingerprint": { "endpoint": "/api/sec/v1/shield/webprofile", "called_count": 2, ... },
    "behavior_tracking": { "endpoint": "/api/v2/collect", "called_count": 54, ... },
    "apm_monitoring": { ... },
    "request_signature": { "scheme": "x-s-common (current)", "coverage_pct": 92.6, ... },
    "cookie_state": { "has_a1": true, "has_web_session": true, ... }
  },
  "category_distribution": { "business_api": 60, "fingerprint_upload": 12, ... },
  "top_hosts": { "edith.xiaohongshu.com": 60, ... },
  "high_risk_signals": [],
  "warnings": []
}
```

### 风险等级判断规则

| risk_level | 触发条件 |
|---|---|
| `high` | 存在 `signature_failure` 类别请求，或 HTTP 999 响应 |
| `medium` | HTTP 401 / 403 / 461，或 `acw_tc` cookie 变更，或 `risk_redirect` |
| `low` | 有 warnings（签名覆盖率不足、行为埋点缺失等） |
| `safe` | 无高风险信号、无 warnings |
| `unknown` | netlog 为空 |

### 实现细节

- `scripts/xhs/risk_analyzer.py` — 纯函数 `analyze(entries)` 模块，无副作用，可独立单测
- `scripts/xhs/bridge.py` — 新增 `get_netlog()` / `get_netlog_enabled()` 方法，调用 background.js 的 `get_netlog` / `get_netlog_enabled` 命令
- `extension/background.js` — handleCommand switch 新增 `case "get_netlog"` / `case "get_netlog_enabled"`，通过 websocket bridge 路径响应（与 popup 内部 chrome.runtime.sendMessage 路径独立）

---

## 附录：XHS 反爬体系深度反推（2026-05-19 实施 + 冒烟）

本会话端到端跑通了 search / fill-publish 两个场景，结合 netlog 实测数据反推了 XHS 完整的反自动化检测体系。

### 五层防护架构

```
┌─ 第 5 层：服务端规则引擎 ────────────┐  ML + 规则混合 → isRiskUser 输出
│  (XHS 自家 + 阿里云风控 SaaS)        │  pass / risk / limit / block
├─ 第 4 层：行为埋点 t2/collect ───────┤  protobuf，每操作 1-10 条
│  (XHS 自家 nameTracker=wapT)         │  操作标签 + 时序 + 上下文
├─ 第 3 层：远程脚本 sec/v1/scripting ─┤  动态下发 JS 在浏览器执行
│  (callFrom=web/creator-platform)     │  采集 mouse/keyboard/检测自动化
├─ 第 2 层：Shield SDK webprofile ─────┤  浏览器/设备指纹加密上报
│  (sdkVersion 4.3.5, profileData hex) │  navigator/canvas/webgl/audio
└─ 第 1 层：WAF 边缘 acw_tc ───────────┘  IP/UA 信誉 + 频率
   (阿里云)                              跨子域统一会话追踪
```

### 关键检测维度（按重要性）

#### 1. 服务端风控判定 `isRiskUser`（最具利用价值）

每个业务 API 调用后，前端 SDK 将服务端的风控判定主动回传到 APM：

```json
POST apm-fe.xiaohongshu.com/api/data
[{
  "measurement_name": "httpRequestTiming",
  "measurement_data": {
    "matchedPath": "/api/sns/web/v1/search/onebox",
    "isRiskUser": "pass",               ← 服务端判定: pass / risk / limit / block
    "isRiskReason": "[]",                ← 触发的规则数组
    "i12": 16, "i13": 32, "i14": 100,    ← 多维评分（命名混淆）
    "traceId": "cf2041020f9322b15...",
    "status": 200, ...
  }
}]
```

**反推**：`i12/i13/i14` 是 0-100 的评分维度（推测：行为序列分 / 指纹分 / 网络环境分）。实测 `unread_count` 这种高频轮询接口 i14=62（中位），普通业务 API i14=100（满分），创作中心 i14=85。

**实战利用**：自动化操作过程中实时读 `isRiskUser`，从 `pass` 变其他状态立即停手。

#### 2. 浏览器指纹 Shield SDK 4.3.5

```json
POST as.xiaohongshu.com/api/sec/v1/shield/webprofile
{
  "platform": "Windows",
  "sdkVersion": "4.3.5",        ← XHS Shield SDK 版本（可追踪迭代）
  "svn": "2",
  "profileData": "c058828ff..."  ← 加密 hex 串（含 navigator/canvas/webgl/audio/字体）
}
```

#### 3. 远程下发脚本 `/api/sec/v1/scripting`（JSONP 模式）

```json
POST /api/sec/v1/scripting
{ "callFrom": "web", "callback": "seccallback" }            ← 主站
{ "callFrom": "creator-platform", "type": "ds", "appId": "ugc" }  ← 创作中心
```

—— 服务端动态下发 JS 在浏览器本地执行，采集 mouse/keyboard timing + 检测 webdriver/Puppeteer 痕迹。`callFrom` 按场景下发不同检测脚本。

#### 4. 行为采集 `t2.xiaohongshu.com/api/v2/collect`

protobuf binary 编码（base64），每个用户操作 1-10 条。明文可见：
- artifact: `xhs-pc-web` 6.11.1
- app: `discovery-undefined` / `ugc`（创作中心）
- device_id: `febeb55be25f2a4093229f58643bd140`（32 字符 hex，跨上报一致）
- user_id, session UUIDs, UA
- **中文操作标签**：`创作者-新发布视频题` / `创作者服务平台` / `official`

#### 5. APM 行为埋点 `apm-fe.xiaohongshu.com/api/data` (wapT SDK)

JSON 明文，每个 API 调用一条 timing 记录，含：
- `context_sdkSessionId / pageSessionId / sdkSeqId`（**单调递增**，跳号即异常）
- `context_deviceId`（与 t2 一致）
- `context_route`（完整页面路由）
- `context_artifactName: "xhs-pc-web"`
- `measurement_name: "httpRequestTiming"` 含 `isRiskUser`（见 #1）

#### 6. 业务 API 签名 `x-s-common`

实测 25/27 业务 API 带 `x-s-common` header。**旧 spec 假设的 `xs / xt` 双签名已废弃**（实测 0 命中）。XHS 演进为 `x-s-common` 单签名方案。

主站签名覆盖率 92%，创作中心 60%（大量 OPTIONS preflight 无签名）。

#### 7. Cookie 一致性追踪

| Cookie | 用途 |
|---|---|
| `a1`（设备指纹种子） | 100% 命中，device_id 派生自 |
| `web_session` | 主站登录态 |
| `webId` / `gid` | 浏览器 ID / 设备识别 |
| `acw_tc`（阿里云 WAF）| **跨子域统一指纹**，每次跨子域跳转重新颁发，强一致追踪 |
| `tgw_l7_route`（阿里云 SLB）| ros-upload 每次 PUT 都换路由 cookie，防 hash 缓存攻击 |
| CAS SSO 链 5 cookie | 创作中心独立 session（见 #9） |

#### 8. ros-upload 上传链路

```
1. GET creator/api/media/v1/upload/creator/permit?biz_name=spectrum&scene=image
   响应: { code:0, data: { uploadTempPermits, result } }  ← 预签名 URL
2. OPTIONS ros-upload.xiaohongshu.com/?speedTestToken=xxx  ← CDN 选优
3. PUT ros-upload.xiaohongshu.com/spectrum/<obj_key>       ← binary 上传
```

`biz_name=spectrum` 是 XHS 内部图片处理服务 / OSS bucket 代号。

#### 9. 创作中心 CAS SSO 鉴权链

创作中心**独立于主站 session**，必须经过 CAS 拿单独的 creator session：

```
1. /publish/publish → 401（无 creator session）
2. → /login?redirectReason=401 重定向
3. → customer.xiaohongshu.com/api/cas/customer/web/zones
4. → customer.xiaohongshu.com/api/cas/customer/web/service-ticket
5. → 5 cookie：customer-sso-sid / x-user-id-creator.xiaohongshu.com /
       access-token-creator.xiaohongshu.com / galaxy_creator_session_id /
       galaxy.creator.beaker.session.id
6. → /publish/publish 带 creator session 重试
```

#### 10. A/B 测试 + 检测规则分层 `racing_get/report`

```json
POST edith.xiaohongshu.com/api/sns/web/racing_get
POST edith.xiaohongshu.com/api/sns/web/racing_report
{
  "racing_info": [
    { "web_id": "febeb55b...", "domain": "web_ab" },     ← 设备级 A/B
    { "user_id": "6919c59d...", "domain": "web_user" }    ← 用户级 A/B
  ],
  "source": "web",
  "app": "creator-publish"
}
```

XHS 给不同用户分配不同检测规则版本（A/B 分流）。同一段自动化代码对不同账号可能效果不同。

### 🚨 反爬陷阱：Honey Pot Tab（实施时新发现）

XHS 创作中心给每个 tab 放**真+假**两份：

```html
<!-- 真 tab：Vue scoped，无 hp 标记，藏在 -9999px 但 pointer-events: auto -->
<div data-v-1ff40f7c data-v-0b179352 class="creator-tab"
     style="position: absolute; left: -9999px; top: -9999px;">
  <span class="title">上传图文</span>
</div>

<!-- 假 tab (honey pot)：data-hp-kind + button-hp-installed，opacity:1e-05 -->
<div class="creator-tab" button-hp-installed="1"
     data-hp-kind="creator-tab-上传图文" aria-hidden="true"
     style="position: absolute; opacity: 1e-05; pointer-events: auto;">
</div>
```

**点击 honey pot 会被标记为机器人 + active class 静默不切换**。正确策略：排除任何带 `data-hp-kind` 或 `button-hp-installed` 属性的元素，只点真 Vue tab。

**`active` class 会故意放在 honey pot 上反向迷惑机器人**（让你以为切换失败）。实际页面已切换，后续 selector 都能工作。

### 反爬规避总策略（给自动化 / Claude）

| 维度 | 风险 | 规避策略 |
|---|---|---|
| `isRiskUser` APM 字段 | 🔥极高（也是机会） | 主动监听：从业务 API 响应间接通过 APM 读取，pass→其他立即停手 |
| Shield profileData | 🔥极高 | 用真实浏览器，禁止覆盖 navigator/screen/canvas/webgl |
| `scripting` 远程脚本 | 🔥极高 | 让其正常下发执行，不要 hook 全局 `seccallback` 函数 |
| **Honey pot tab 陷阱** | 🔥高 | 排除 `data-hp-kind` / `button-hp-installed` 元素 |
| device_id 一致性 | 🔥高 | 不清 a1 cookie，不切 device_id |
| 鼠标轨迹 / 键盘节奏 | 高 | Bezier 曲线 mouse move + 字符间随机 50-150ms + 0.5% 概率打错-删-重打 |
| 操作时序 | 高 | 每个 action 间 N(2.5s, 0.8s) 高斯停顿；偶尔 5-10s 长停顿模拟"看一下" |
| t2 protobuf 埋点 | 中 | 不屏蔽（缺失反而异常），节奏自然 |
| `acw_tc` 跨域 | 中 | 不阻止 Set-Cookie，让 WAF 跟踪正常 |
| CAS SSO（创作中心）| 中 | 必须走完 CAS 拿 creator session 5-cookie 链 |
| racing A/B 分流 | 低 | 让其正常执行；保持账号年龄/行为画像 |

### Response.prototype hook 必要性

XHS 主 bundle 加载时用混淆代码（`_garp_xxx`）覆盖 `window.fetch`，绕过我们 `document_start` 装的 fetch hook。**Response.prototype.text/.json 是不可绕过的 hook 点**：任何代码读响应体必须调这两个方法之一。

实施细节：interceptor.js 在 IIFE 顶部 capture `Response.prototype.text/.json` 引用，替换为我们的版本，原方法被调用时 postMessage 上报响应体。

### 实施期间发现的 Bug 修复

| 类型 | 修复 |
|---|---|
| **interceptor.js syntax error** | 4 处中文字符串嵌套未转义双引号（line 100/194/214/480），导致**整个 interceptor.js 从未成功 parse**。修：内部 `""` 改为中文「」 |
| **状态同步链路竞态** | interceptor 等 content.js postMessage 启用状态期间所有 fetch 被跳过。修：interceptor 总是上报，background `netlogIngestInterceptor` 单点过滤 |
| **URL 匹配 protocol-relative** | edith 业务 API 用 `//edith...` 协议相对 URL，与 webRequest 的绝对 URL 不等。修：`_netlogUrlMatch` 加 base URL 兼容 |
| **fetch wrapper 覆盖** | XHS `_garp_xxx` 直接覆盖 `window.fetch`。修：改 hook Response.prototype |
| **publish.py honey pot 陷阱** | 旧 selector 点了 `data-hp-kind` 假 tab。修：排除 hp 属性 + 等 active 切换确认 |
| **REQBODY_MAX 2KB 截断** | APM 上报含 isRiskUser 的 JSON 1-3KB，2048 字节截断导致 JSON parse 失败。修：增大到 8192 |
| **risk_analyzer regex 容错** | reqBody 仍可能截断时 JSON parse 失败。修：正则提取 isRiskUser/isRiskReason/i12-i14，不依赖完整 JSON |

### 已知限制 / 未来改进

- 账号切换不自动 clear netlog（用户需手动清空）
- 跨域风控上报域响应体看不到（webRequest 限制 + 跨域无 interceptor）
- 同一 url 短时间高并发请求时关联可能漏配（2s 时间窗 + url 模糊匹配）
- `publish.py` 中 `_wait_for_upload_complete` 也可能命中类似 honey pot（待用户实测确认）
- 当前账号已建立信任画像，所有实测 isRiskUser 均为 `pass`。要实测 `risk/limit/block` 需要：① 高频机械操作触发降评分，或 ② 在新账号上测试

### 端到端冒烟实测结果

| 场景 | total | isRiskUser | acw_tc 变更 | x-s-common 覆盖 | 主要 host |
|---|---|---|---|---|---|
| 浏览/搜索 (search "claude") | 146 | 全 pass (40/40) | 1 | 92% | t2(64) edith(27) apm-fe(25) |
| 搜索 "ai" | 99 | 全 pass (27/27) | 0 | - | apm-fe(28) t2(28) edith(14) |
| **创作中心填表** | 284 | 全 pass (7/7) | 1 | 60% | apm-fe(108) t2(71) as(19) edith(19) creator(16) ros-upload(3) |

