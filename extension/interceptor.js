/**
 * XHS 404 诊断拦截器 — MAIN world, document_start
 *
 * 在小红书页面 JS 加载之前包裹原生 fetch / XMLHttpRequest，
 * 捕获所有返回 404 / 461 / 403 / 999 的请求的完整上下文，
 * 进行根因分析后通过 window.postMessage 发送给 content script。
 *
 * 诊断覆盖：
 *   - xsec_token 缺失 / 绑定失效
 *   - xs 签名缺失 / 无效
 *   - web_session 失效（未登录）
 *   - IP / 账号级封禁
 *   - 页面渲染 404（HTTP 200 但内容被屏蔽）
 */

(function () {
  "use strict";

  const BLOCKED = new Set([404, 461, 403, 999]);
  const XHS_API = /xiaohongshu\.com\/api\//;

  const RESP_BODY_MAX = 4096;

  // ── Cookie 快照 ──────────────────────────────────────────────

  function captureCookies() {
    const map = {};
    for (const part of document.cookie.split(";")) {
      const idx = part.indexOf("=");
      if (idx < 0) continue;
      const k = part.slice(0, idx).trim();
      const v = part.slice(idx + 1).trim();
      map[k] = v;
    }
    return {
      has_a1:          "a1" in map,
      has_web_session: "web_session" in map,
      has_webId:       "webId" in map,
      has_gid:         "gid" in map,
      // 部分预览（不暴露完整 token）
      a1_preview:          map["a1"]          ? map["a1"].slice(0, 12)          + "…" : null,
      web_session_preview: map["web_session"] ? map["web_session"].slice(0, 10) + "…" : null,
    };
  }

  // ── URL 解析 ─────────────────────────────────────────────────

  function urlParam(url, key) {
    try { return new URL(url).searchParams.get(key); } catch (_) { return null; }
  }

  function isApiUrl(url) { return XHS_API.test(url); }

  // ── 根因分析 ─────────────────────────────────────────────────

  function diagnose(status, url, headers, cookies) {
    const xsecToken  = urlParam(url, "xsec_token");
    const xsecSource = urlParam(url, "xsec_source");
    const hasXs  = !!(headers["xs"] || headers["X-S"] || headers["x-s"]);
    const hasXt  = !!(headers["xt"] || headers["X-T"] || headers["x-t"]);
    const isPage = !isApiUrl(url);

    // ── 999：系统级封禁 ──────────────────────────────────────
    if (status === 999) {
      return {
        root_cause: "账号 / IP 被系统级封禁（HTTP 999）",
        cause_category: "account_block",
        detail:
          "小红书用 HTTP 999 标记被彻底封禁的账号或 IP 段，" +
          "所有请求无论携带什么凭证均被拒绝。需更换 IP 或重新注册账号。",
        confidence: "high",
        how_xhs_decides:
          "服务端在路由层维护封禁名单（IP CIDR + userId），命中即返回 999，不经业务逻辑。",
      };
    }

    // ── 403：WAF / 防火墙 ────────────────────────────────────
    if (status === 403) {
      return {
        root_cause: "请求被 WAF / 前置防火墙拦截",
        cause_category: "ip_block",
        detail:
          "HTTP 403 来自小红书的 WAF 层（非业务层），" +
          "常见触发条件：IP 信誉分过低、User-Agent 异常、请求速率超阈值、" +
          "或请求头特征命中自动化规则。",
        confidence: "high",
        how_xhs_decides:
          "WAF 对每个请求计算特征向量：IP 信誉 × UA 合规性 × 请求间隔 × 头部完整性。" +
          "超过风险阈值时直接返回 403，不转发给后端。",
      };
    }

    // ── 461：签名问题 ────────────────────────────────────────
    if (status === 461) {
      if (!hasXs) {
        return {
          root_cause: "xs 请求签名完全缺失",
          cause_category: "signature",
          detail:
            "HTTP 461 是小红书专用的「签名缺失」状态码。" +
            "所有 /api/ 接口均要求 xs header（HMAC 签名），未携带时直接返回 461。",
          confidence: "high",
          how_xhs_decides:
            "服务端在 API 网关层检查 xs header 是否存在。" +
            "xs = HMAC(url_path + body_hash + timestamp, device_key)，" +
            "缺失 → 461，存在但验签失败 → 也是 461（但 detail 不同）。",
        };
      }
      return {
        root_cause: `xs 签名存在但验证失败${hasXt ? "" : "（xt 时间戳头也缺失）"}`,
        cause_category: "signature",
        detail:
          "xs header 已附加，但服务端 HMAC 验证未通过。" +
          "可能原因：\n" +
          "  1. 签名算法版本过旧（XHS 定期更新 xs 算法，v1→v2→v3…）\n" +
          "  2. device_id 与 a1 cookie 不对应（签名密钥绑定设备）\n" +
          `  3. xt 时间戳偏差超出允许窗口（±5 分钟）${!hasXt ? "，且 xt 头缺失" : ""}`,
        confidence: "high",
        how_xhs_decides:
          "服务端用从 a1 cookie 推导的 device_key 重新计算 HMAC，" +
          "对比请求中的 xs 值；同时校验 xt 时间戳与服务器时钟差是否在容忍窗口内。",
      };
    }

    // ── 404：多种场景 ────────────────────────────────────────

    // 页面级 404（非 API）
    if (isPage) {
      if (!xsecToken) {
        return {
          root_cause: "xsec_token 缺失——直接构造 URL 访问",
          cause_category: "token",
          detail:
            "小红书笔记 / 用户主页 URL 必须携带 xsec_token 参数，" +
            "否则服务端直接返回 404（故意用 404 而非 403，迷惑爬虫以为内容不存在）。\n\n" +
            "正确获取方式：从搜索结果 / 推荐流 / 分享链接中提取，不可手动构造。",
          confidence: "high",
          how_xhs_decides:
            "服务端对所有笔记详情请求校验 xsec_token 签名；" +
            "token = HMAC(noteId + sessionContext, serverKey)，缺失直接 404。",
        };
      }
      if (!cookies.has_web_session) {
        return {
          root_cause: "xsec_token 有效，但 web_session cookie 不存在（未登录 / session 失效）",
          cause_category: "session",
          detail:
            "xsec_token 参数存在，但 web_session cookie 缺失或已过期。" +
            "服务端绑定验证：token 必须与颁发时的 session 匹配，" +
            "session 失效后 token 同步失效，返回 404（而非重定向到登录页，这是故意的）。",
          confidence: "high",
          how_xhs_decides:
            "服务端将 xsec_token 与 web_session 绑定存储；" +
            "请求时查 session 是否存在，不存在则 token 无法解密 → 404。",
        };
      }
      return {
        root_cause: `xsec_token 与当前 session / IP 绑定验证失败（来源: ${xsecSource || "未知"}）`,
        cause_category: "token",
        detail:
          "xsec_token 和 web_session 均存在，但服务端绑定验证失败。\n" +
          "可能原因：\n" +
          "  1. token 是从其他账号获取的（token 绑定颁发时的 userId）\n" +
          "  2. token 已超过有效期（通常数小时到 1 天）\n" +
          "  3. IP 变化触发服务端将 token 标记为可疑\n" +
          `  4. xsec_source="${xsecSource}" 来源类型不符（如 token 从 search 获取却用于 profile 页）`,
        confidence: "medium",
        how_xhs_decides:
          "服务端用 serverKey 解密 xsec_token，提取 {noteId, userId, source, ts, ipHash}，" +
          "逐字段与当前请求对比：任一不符则 404。",
      };
    }

    // API 级 404
    if (!cookies.has_web_session) {
      return {
        root_cause: "API 请求未携带有效 session（未登录）",
        cause_category: "session",
        detail:
          "API 端点在 web_session 不存在时返回 404（而非 401），" +
          "这是小红书对未登录访问私有 API 的有意混淆处理。",
        confidence: "high",
        how_xhs_decides:
          "API 网关在 session 校验失败时根据端点配置选择响应码：" +
          "公开 API 返回空数据，私有 API 直接返回 404 以阻止枚举。",
      };
    }
    if (!hasXs) {
      return {
        root_cause: "此 API 端点在签名缺失时返回 404（比 461 更严格的端点）",
        cause_category: "signature",
        detail:
          "部分高敏感 API 端点（通常是读取用户隐私数据类）在签名缺失时直接返回 404 而非 461，" +
          "使爬虫难以区分「不存在」和「无权访问」。",
        confidence: "medium",
        how_xhs_decides:
          "API 网关根据端点安全级别配置响应码策略：" +
          "普通端点 → 461，高敏感端点 → 404，以防止端点存在性探测。",
      };
    }
    return {
      root_cause: "IP 或账号维度风控封禁（所有凭证均有效但仍 404）",
      cause_category: "risk_control",
      detail:
        "web_session、xs 签名、xsec_token 均存在且通过格式校验，" +
        "但服务端仍返回 404。这是最典型的风控特征：\n" +
        "  - IP 风控：当前 IP 在短时间内请求量超过阈值，被标记后所有内容请求返回 404\n" +
        "  - 账号风控：账号行为评分低于阈值（过于机械的操作模式），内容访问被限流\n" +
        "  - 设备指纹风控：浏览器指纹被识别为自动化环境",
      confidence: "high",
      how_xhs_decides:
        "服务端对每个 {IP, userId, deviceId} 三元组维护实时行为评分：" +
        "请求间隔方差、路径分布、滚动事件频率等，评分低于阈值时对特定内容返回 404，" +
        "但登录态保持正常（避免用户察觉），造成「内容消失」的假象。",
    };
  }

  // ── 构造完整诊断事件 ─────────────────────────────────────────

  function buildEvent(url, method, status, headers, extra) {
    const cookies = captureCookies();
    const diag    = diagnose(status, url, headers, cookies);
    return {
      id:        `${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      timestamp: new Date().toISOString(),
      url,
      method:    method.toUpperCase(),
      status,
      pageUrl:   window.location.href,
      request: {
        xsec_token:    urlParam(url, "xsec_token"),
        xsec_source:   urlParam(url, "xsec_source"),
        has_xs:        !!(headers["xs"]      || headers["X-S"]           || headers["x-s"]),
        has_xt:        !!(headers["xt"]      || headers["X-T"]           || headers["x-t"]),
        has_referer:   !!(headers["referer"] || headers["Referer"]),
        sec_fetch_site: headers["Sec-Fetch-Site"] || headers["sec-fetch-site"] || null,
        content_type:   headers["Content-Type"]   || headers["content-type"]   || null,
      },
      cookies,
      diagnosis: diag,
      ...(extra || {}),
    };
  }

  // 消息队列：content.js 在 document_idle 才就绪，早于此时的事件需要排队
  const _pendingEvents = [];
  let _contentReady = false;

  window.addEventListener("message", (e) => {
    if (e.data?.source === "xhs-interceptor-ack") _contentReady = true;
  });

  function emit(event) {
    if (_contentReady) {
      window.postMessage({ source: "xhs-interceptor", type: "BLOCK_EVENT", event }, "*");
    } else {
      _pendingEvents.push(event);
    }
  }

  function flushPending() {
    _contentReady = true;
    for (const ev of _pendingEvents) {
      window.postMessage({ source: "xhs-interceptor", type: "BLOCK_EVENT", event: ev }, "*");
    }
    _pendingEvents.length = 0;
  }

  // content.js 就绪后会发 xhs-interceptor-ready 消息，或 500ms 后强制 flush
  window.addEventListener("message", (e) => {
    if (e.data?.source === "xhs-content-ready") flushPending();
  });
  setTimeout(flushPending, 800);

  // ── Response.prototype hook ─────────────────────────────────
  // XHS 在主 bundle 加载时会用混淆代码覆盖 window.fetch，绕过我们的 fetch hook。
  // 改为 hook Response.prototype.text/.json：任何代码读响应体都必须调这两个之一，
  // 包括 XHS 的 wrapper 链最终也得到 Response 对象。

  const _respText = Response.prototype.text;
  const _respJson = Response.prototype.json;

  function _netlogReportResp(url, status, body) {
    if (!url || !url.includes("xiaohongshu.com")) return;
    let truncated = body;
    if (typeof body === "string" && body.length > RESP_BODY_MAX) {
      truncated = body.slice(0, RESP_BODY_MAX) + "…[cut]";
    }
    try {
      window.postMessage({
        source: "xhs-netlog-intercept",
        method: "?",                // Response 对象拿不到原 method
        url,
        status,
        reqHeaders: {},
        respBody: truncated,
        ts: Date.now(),
      }, "*");
    } catch (_) {}
  }

  Response.prototype.text = async function() {
    const body = await _respText.call(this);
    _netlogReportResp(this.url, this.status, body);
    return body;
  };

  Response.prototype.json = async function() {
    const data = await _respJson.call(this);
    try {
      _netlogReportResp(this.url, this.status, JSON.stringify(data));
    } catch (_) {}
    return data;
  };

  // ── fetch 拦截 ───────────────────────────────────────────────

  const _fetch = window.fetch;
  window.fetch = async function (input, init) {
    const url    = typeof input === "string" ? input : input?.url || String(input);
    const method = init?.method || "GET";
    const headers = {};
    if (init?.headers) {
      if (init.headers instanceof Headers) {
        init.headers.forEach((v, k) => { headers[k] = v; });
      } else {
        Object.assign(headers, init.headers);
      }
    }

    const resp = await _fetch.call(this, input, init);

    // 现有 BLOCKED 诊断保持不变
    if (BLOCKED.has(resp.status) && (isApiUrl(url) || url.includes("xiaohongshu.com"))) {
      emit(buildEvent(url, method, resp.status, headers, { intercept_type: "fetch" }));
    }

    // NetLog 全量记录（background 单点过滤 _netEnabled）
    if (url.includes("xiaohongshu.com")) {
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

  // ── XMLHttpRequest 拦截 ──────────────────────────────────────

  const _xhrOpen      = XMLHttpRequest.prototype.open;
  const _xhrSend      = XMLHttpRequest.prototype.send;
  const _xhrSetHeader = XMLHttpRequest.prototype.setRequestHeader;

  XMLHttpRequest.prototype.open = function (method, url) {
    this.__i_method  = method;
    this.__i_url     = url;
    this.__i_headers = {};
    return _xhrOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
    if (this.__i_headers) this.__i_headers[name] = value;
    return _xhrSetHeader.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function () {
    this.addEventListener("loadend", () => {
      const url = this.__i_url || "";

      // 现有 BLOCKED 诊断保持不变
      if (BLOCKED.has(this.status) && (isApiUrl(url) || url.includes("xiaohongshu.com"))) {
        emit(buildEvent(url, this.__i_method || "GET", this.status, this.__i_headers || {}, {
          intercept_type: "xhr",
        }));
      }

      // NetLog 全量记录（background 单点过滤 _netEnabled）
      if (url.includes("xiaohongshu.com")) {
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

  // ── 页面渲染级 404 检测（HTTP 200 但内容被屏蔽）───────────────

  function checkPageRender404() {
    const url = window.location.href;
    if (!url.includes("xiaohongshu.com")) return;

    let triggered = false;
    let pageErrorDetail = null;

    // 检查 __INITIAL_STATE__ 中的错误标记
    try {
      const s = window.__INITIAL_STATE__;
      if (s) {
        if (s.pageError || s.errorCode || s.forbidden) {
          triggered = true;
          pageErrorDetail = {
            pageError:  s.pageError  || null,
            errorCode:  s.errorCode  || null,
            forbidden:  s.forbidden  || null,
          };
        }
      }
    } catch (_) {}

    // 检查 DOM
    if (!triggered) {
      const is404Dom = document.title.includes("404") ||
        !!document.querySelector('[class*="not-found"], [class*="error-page"], [class*="page-not-found"]');
      if (is404Dom) triggered = true;
    }

    if (triggered) {
      const cookies = captureCookies();
      const xsecToken = urlParam(url, "xsec_token");
      emit({
        id:        `${Date.now()}_render`,
        timestamp: new Date().toISOString(),
        url,
        method:    "GET",
        status:    "200→404",  // HTTP 成功但渲染出 404
        pageUrl:   url,
        intercept_type: "page_render",
        request: {
          xsec_token:  xsecToken,
          xsec_source: urlParam(url, "xsec_source"),
          has_xs:      false,
          has_xt:      false,
          has_referer: !!document.referrer,
          sec_fetch_site: null,
          content_type:   null,
        },
        cookies,
        page_error_state: pageErrorDetail,
        diagnosis: xsecToken
          ? {
              root_cause: "内容已被删除 / 下架，或账号无权访问",
              cause_category: "content_unavailable",
              detail:
                "页面返回 HTTP 200 但渲染为 404 错误页，且 xsec_token 存在（说明不是 token 问题）。\n" +
                "这表示内容本身已不可用：笔记被作者删除、被平台下架、或当前账号被限制访问该内容。",
              confidence: "medium",
              how_xhs_decides:
                "XHS 前端（Vue/React 应用）在拿到服务端数据后检查 note.status 字段，" +
                "若为 banned/deleted/invisible 则渲染 404 组件，HTTP 状态码仍是 200。",
            }
          : {
              root_cause: "xsec_token 缺失，服务端返回空内容，前端渲染 404",
              cause_category: "token",
              detail: "URL 不含 xsec_token，服务端返回了「内容不存在」的数据包，前端据此渲染 404 页面。",
              confidence: "high",
              how_xhs_decides:
                "同页面级 404 逻辑：token 缺失 → 服务端返回空 note 数据 → 前端渲染 notFound 组件。",
            },
      });
    }
  }

  // 在 DOMContentLoaded 之后检查页面状态
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setTimeout(checkPageRender404, 500));
  } else {
    setTimeout(checkPageRender404, 500);
  }
})();
