/**
 * XHS Bridge - Background Service Worker
 *
 * 连接 Python bridge server（ws://localhost:9333），接收命令并执行：
 * - navigate / wait_for_load: chrome.tabs.update + onUpdated
 * - evaluate / has_element 等: chrome.scripting.executeScript (MAIN world)
 * - click / input 等 DOM 操作: chrome.tabs.sendMessage → content.js
 * - screenshot: chrome.tabs.captureVisibleTab
 * - get_cookies: chrome.cookies.getAll
 */

importScripts("netlogger.js");
netlogInit().catch(e => console.warn("[XHS NetLogger] init failed", e));

const BRIDGE_URL = "ws://localhost:9333";
let ws = null;

// 保持 service worker 存活：有开放的 WebSocket 连接时 Chrome 不会终止 SW
// 额外加 alarm 作为保底
chrome.alarms.create("keepAlive", { periodInMinutes: 0.4 });
chrome.alarms.onAlarm.addListener(() => {
  if (!ws || ws.readyState !== WebSocket.OPEN) connect();
});

// ───────────────────────── WebSocket ─────────────────────────

function setStatus(connected) {
  chrome.storage.session.set({ wsConnected: connected });
}

function broadcastStatus() {
  const status = { wsConnected: ws !== null && ws.readyState === WebSocket.OPEN };
  chrome.runtime.sendMessage({ type: "STATUS_CHANGED", status }).catch(() => {});
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "GET_STATUS") {
    sendResponse({
      success: true,
      status: { wsConnected: ws !== null && ws.readyState === WebSocket.OPEN },
    });
    return true;
  }

  if (msg.type === "ANALYZE_RISK_CONTROL") {
    cmdAnalyzeRiskControl({ probeUrls: msg.probeUrls || [] })
      .then(report => sendResponse(report))
      .catch(e => sendResponse({ error: String(e.message || e) }));
    return true;
  }

  if (msg.type === "XHS_BLOCK_EVENT") {
    storeBlockEvent(msg.event).catch(() => {});
    // 通知 popup 实时刷新
    chrome.runtime.sendMessage({ type: "BLOCK_EVENT_ADDED", event: msg.event }).catch(() => {});
    return false;
  }

  if (msg.type === "GET_404_DIAGNOSTICS") {
    chrome.storage.session.get("blockEvents", (data) => {
      sendResponse({ events: data.blockEvents || [] });
    });
    return true;
  }

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

  if (msg.type === "NETLOG_INTERCEPTOR_ENTRY") {
    netlogIngestInterceptor(msg.payload);
    return false;
  }
});

function connect() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;

  ws = new WebSocket(BRIDGE_URL);

  ws.onopen = () => {
    console.log("[XHS Bridge] 已连接到 bridge server");
    ws.send(JSON.stringify({ role: "extension" }));
    setStatus(true);
    broadcastStatus();
  };

  ws.onmessage = async (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    try {
      const result = await handleCommand(msg);
      ws.send(JSON.stringify({ id: msg.id, result: result ?? null }));
    } catch (err) {
      ws.send(JSON.stringify({ id: msg.id, error: String(err.message || err) }));
    }
  };

  ws.onclose = () => {
    console.log("[XHS Bridge] 连接断开，3s 后重连...");
    setStatus(false);
    broadcastStatus();
    setTimeout(connect, 3000);
  };

  ws.onerror = (e) => {
    console.error("[XHS Bridge] WS 错误", e);
  };
}

// ───────────────────────── 命令路由 ─────────────────────────

async function handleCommand(msg) {
  const { method, params = {} } = msg;

  switch (method) {
    // ── 导航 ──
    case "navigate":
      return await cmdNavigate(params);

    case "wait_for_load":
      return await cmdWaitForLoad(params);

    // ── 截图 ──
    case "screenshot_element":
      return await cmdScreenshot(params);

    case "set_file_input":
      return await cmdSetFileInputViaDebugger(params);

    case "click_element":
    case "click_nth_element":
    case "click_element_by_text":
      return await cmdClickViaDebugger(method, params);

    case "press_key":
      return await cmdPressKeyViaDebugger(params);

    case "type_text":
      return await cmdTypeTextViaDebugger(params);

    // ── NetLog 风控数据 ──
    case "get_netlog":
      return { entries: netlogGetAll(), enabled: netlogIsEnabled() };

    case "get_netlog_enabled":
      return { enabled: netlogIsEnabled() };

    // ── 风控分析 ──
    case "analyze_risk_control":
      return await cmdAnalyzeRiskControl(params);

    case "get_404_diagnostics": {
      const data = await chrome.storage.session.get("blockEvents");
      return data.blockEvents || [];
    }

    case "clear_404_diagnostics":
      await chrome.storage.session.set({ blockEvents: [] });
      return null;

    // ── Cookies ──
    case "get_cookies":
      return await cmdGetCookies(params);

    // ── 在页面主 world 执行 JS（可访问 window.__INITIAL_STATE__ 等） ──
    case "evaluate":
    case "wait_dom_stable":
    case "wait_for_selector":
    case "has_element":
    case "get_elements_count":
    case "get_element_text":
    case "get_element_attribute":
    case "get_scroll_top":
    case "get_viewport_height":
    case "get_url":
    case "get_elements_info":
    case "get_iframe_text":
      return await cmdEvaluateInMainWorld(method, params);

    // ── DOM 操作（在页面 MAIN world 执行，无需 content script 就绪） ──
    default:
      return await cmdDomInMainWorld(method, params);
  }
}

// ───────────────────────── 导航 ─────────────────────────

/**
 * 导航完成后立即在 MAIN world 检测页面是否为 404 / 风控拦截页。
 * 这是捕获导航级 404 的唯一可靠时机（fetch/XHR 拦截器看不到 browser navigation）。
 */
async function detectNavigationBlock(tabId, navigatedUrl, finalUrl) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    func: (targetUrl, landedUrl) => {
      // ── 1. 读 cookie 快照 ──────────────────────────────────
      const cookieMap = {};
      for (const p of document.cookie.split(";")) {
        const i = p.indexOf("=");
        if (i < 0) continue;
        cookieMap[p.slice(0, i).trim()] = p.slice(i + 1).trim();
      }
      const cookies = {
        has_a1:          "a1" in cookieMap,
        has_web_session: "web_session" in cookieMap,
        has_webId:       "webId" in cookieMap,
        a1_preview:          cookieMap["a1"]          ? cookieMap["a1"].slice(0, 12)          + "…" : null,
        web_session_preview: cookieMap["web_session"] ? cookieMap["web_session"].slice(0, 10) + "…" : null,
      };

      // ── 2. 解析 xsec_token（从原始目标 URL）────────────────
      let xsecToken = null, xsecSource = null;
      try {
        const u = new URL(targetUrl);
        xsecToken  = u.searchParams.get("xsec_token");
        xsecSource = u.searchParams.get("xsec_source");
      } catch (_) {}

      // ── 3. 判断是否为 404 / 风控拦截页 ───────────────────

      // 优先：检查最终落地 URL 是否为 /404 重定向（最可靠的信号）
      let blockType = null;
      let httpLike404 = false;
      let redirectSource = null;

      if (landedUrl && /xiaohongshu\.com\/404(\?|$)/.test(landedUrl)) {
        blockType = "redirect_404";
        httpLike404 = true;
        try {
          redirectSource = new URL(landedUrl).searchParams.get("source") || null;
        } catch (_) {}
      }

      // 备选：页面 title 含 404
      if (!blockType) {
        const title = document.title || "";
        if (title.includes("404") || title.includes("Not Found")) {
          blockType = "http_404";
          httpLike404 = true;
        }
      }

      // SPA 渲染的 404（HTTP 200 但内容不存在）
      // 特征：__INITIAL_STATE__ 有错误标记，或特定 DOM 元素存在
      if (!blockType) {
        try {
          const s = window.__INITIAL_STATE__;
          if (s) {
            if (s.pageError || s.errorCode || s.forbidden || s.noteError) {
              blockType = "spa_404";
            }
            // 笔记详情页：note 对象存在但 status 为下架/删除
            const note = s?.note?.noteDetailMap
              ? Object.values(s.note.noteDetailMap)[0]?.note
              : null;
            if (note && (note.status === "banned" || note.status === "deleted" || note.status === "invisible")) {
              blockType = "content_removed";
            }
          }
        } catch (_) {}
      }

      // DOM 指纹检测
      if (!blockType) {
        const notFoundEl =
          document.querySelector('[class*="not-found"]') ||
          document.querySelector('[class*="error-page"]') ||
          document.querySelector('[class*="page-404"]');
        if (notFoundEl) blockType = "spa_404";
      }

      // 风控验证弹窗（不是 404，但也要记录）
      if (!blockType) {
        const captchaEl =
          document.querySelector('[class*="captcha"]') ||
          document.querySelector('[class*="verify"]') ||
          document.querySelector("#captcha-container");
        if (captchaEl) blockType = "captcha_block";
      }

      if (!blockType) return null;  // 正常页面，不记录

      // ── 4. 根因分析 ──────────────────────────────────────
      let diagnosis;
      const isPage = !/\/api\//.test(targetUrl);

      if (blockType === "redirect_404") {
        // /404?source= 是 XHS 302 重定向风控的标准形式，根因最确定
        if (!cookies.has_web_session) {
          diagnosis = {
            root_cause: "未登录 / web_session 失效，服务端 302 重定向到 /404",
            cause_category: "session",
            detail:
              `XHS 服务端检测到请求无有效 session，执行 302 重定向到 /404?source=${redirectSource || targetUrl}。\n` +
              "web_session cookie 不存在或已过期，xsec_token 随之失效。\n" +
              "解决：重新登录获取新的 web_session。",
            confidence: "high",
            how_xhs_decides:
              "服务端在路由层校验 web_session 有效性，失效时直接 302 到 /404，" +
              "source 参数记录原始请求路径供前端展示。",
          };
        } else if (!xsecToken) {
          diagnosis = {
            root_cause: "xsec_token 缺失，服务端 302 重定向到 /404",
            cause_category: "token",
            detail:
              `原始路径 ${redirectSource || targetUrl} 未携带 xsec_token，服务端立即 302 到 /404。\n` +
              "必须从搜索 / 推荐流获取包含 xsec_token 的完整 URL，不能直接构造。",
            confidence: "high",
            how_xhs_decides:
              "CDN / 路由层在 URL 路由前校验 xsec_token 存在性，缺失则 302 到 /404，" +
              "早于业务逻辑执行，source 参数为原始路径。",
          };
        } else {
          diagnosis = {
            root_cause: `xsec_token 绑定验证失败，服务端 302 重定向到 /404（来源: ${xsecSource || "未知"}）`,
            cause_category: "token",
            detail:
              `token 和 session 均存在，但服务端解密 xsec_token 后验证失败，302 到 /404?source=${redirectSource || targetUrl}。\n` +
              "可能原因：\n" +
              "  1. xsec_token 超过有效期（通常数小时）\n" +
              "  2. IP 变化导致 token 中的 ipHash 不匹配\n" +
              `  3. xsec_source="${xsecSource}" 来源类型与当前访问场景不符\n` +
              "  4. token 从其他账号获取（token 绑定颁发时的 userId）",
            confidence: "high",
            how_xhs_decides:
              "服务端解密 xsec_token：{noteId, userId, ipHash, ts, source}，逐字段比对当前请求，" +
              "任一不符则 302 到 /404，source 参数为被拒绝的原始路径。",
          };
        }
      } else if (blockType === "captcha_block") {
        diagnosis = {
          root_cause: "触发人机验证（验证码弹窗）",
          cause_category: "captcha",
          detail:
            "页面弹出验证码，通常由短时间内大量请求或异常行为模式触发。" +
            "XHS 的风控系统在请求频率 / 行为评分超阈值时插入验证流程而非直接封禁。",
          confidence: "high",
          how_xhs_decides:
            "服务端对 {IP, userId} 维护请求频率计数，超过阈值时在响应中注入验证码触发标记，" +
            "前端 JS 检测到标记后渲染验证码组件。",
        };
      } else if (!xsecToken && isPage) {
        diagnosis = {
          root_cause: "xsec_token 缺失——直接导航到笔记 URL",
          cause_category: "token",
          detail:
            "URL 不含 xsec_token 参数，服务端返回 404（故意用 404 而非 403 迷惑爬虫）。\n" +
            "正确做法：必须从搜索 / 推荐流获取含 xsec_token 的完整 URL。",
          confidence: "high",
          how_xhs_decides:
            "CDN / API 网关在路由层校验 xsec_token 存在性，缺失直接短路返回 404。",
        };
      } else if (!cookies.has_web_session && isPage) {
        diagnosis = {
          root_cause: "web_session 失效（未登录），token 同步失效",
          cause_category: "session",
          detail:
            "xsec_token 存在但 web_session cookie 已失效。" +
            "服务端将 token 与颁发时的 session 绑定，session 失效后 token 自动作废，返回 404。",
          confidence: "high",
          how_xhs_decides:
            "服务端用 web_session 解密 xsec_token 中的加密字段，" +
            "session 失效则解密失败 → 404。",
        };
      } else if (blockType === "content_removed") {
        diagnosis = {
          root_cause: "内容已被删除 / 下架（note.status 异常）",
          cause_category: "content_unavailable",
          detail:
            "HTTP 200 正常返回，但 __INITIAL_STATE__ 中的 note.status 为 banned / deleted / invisible，" +
            "前端据此渲染 404 组件。与 URL 或 session 无关，是内容本身的状态问题。",
          confidence: "high",
          how_xhs_decides:
            "服务端在 note 数据中携带 status 字段，前端 Vue 组件检查此字段，" +
            "非 normal 状态则渲染 notFound 页面，HTTP 状态码仍是 200。",
        };
      } else if (xsecToken && cookies.has_web_session) {
        diagnosis = {
          root_cause: `xsec_token 与当前 session / IP 绑定验证失败（来源: ${xsecSource || "未知"}）`,
          cause_category: "token",
          detail:
            "token 和 session 均存在，但服务端验证绑定关系失败。\n" +
            "可能原因：\n" +
            "  1. token 超过有效期（通常数小时）\n" +
            "  2. IP 变化导致 token 中的 ipHash 不匹配\n" +
            `  3. xsec_source="${xsecSource}" 与实际访问场景不符`,
          confidence: "medium",
          how_xhs_decides:
            "服务端解密 xsec_token 后逐字段校验：noteId、userId、ipHash、ts、source，任一不符 → 404。",
        };
      } else {
        diagnosis = {
          root_cause: "IP / 账号级风控封禁（凭证齐全但仍被拦截）",
          cause_category: "risk_control",
          detail:
            "所有凭证均存在，但页面仍渲染 404。" +
            "这是 XHS 对高风险 IP / 账号的内容屏蔽策略：" +
            "不直接封号，而是让特定内容【消失】，降低用户对封禁的察觉。",
          confidence: "medium",
          how_xhs_decides:
            "服务端对 {IP, userId, deviceId} 三元组计算行为评分，" +
            "低于阈值时对内容请求返回 404，登录态保持正常。",
        };
      }

      return {
        id:             `${Date.now()}_nav`,
        timestamp:      new Date().toISOString(),
        url:            targetUrl,
        final_url:      landedUrl || window.location.href,
        redirect_source: redirectSource,
        method:         "GET",
        status:         httpLike404 ? 404 : "200→404",
        pageUrl:        window.location.href,
        intercept_type: "navigation",
        block_type:     blockType,
        request: {
          xsec_token:    xsecToken,
          xsec_source:   xsecSource,
          has_xs:        false,   // 导航请求不含 xs header
          has_xt:        false,
          has_referer:   !!document.referrer,
          sec_fetch_site: null,
          content_type:   null,
        },
        cookies,
        diagnosis,
      };
    },
    args: [navigatedUrl, finalUrl || ""],
  });

  const event = results?.[0]?.result;
  if (event) {
    await storeBlockEvent(event);
    chrome.runtime.sendMessage({ type: "BLOCK_EVENT_ADDED", event }).catch(() => {});
    console.log(`[XHS Bridge] 导航 404 已捕获: ${event.diagnosis.root_cause}`);
  }
  return event || null;
}

// ── 从 URL 中提取笔记 ID ────────────────────────────────────
function extractNoteId(url) {
  const m = url.match(/\/explore\/([a-f0-9]{20,})/);
  return m ? m[1] : null;
}

// ── 通过搜索页获取笔记的最新 xsec_token ─────────────────────
async function getFreshXsecToken(tabId, noteId) {
  const searchUrl =
    "https://www.xiaohongshu.com/search_result?" +
    `keyword=${encodeURIComponent(noteId)}&type=51&source=web_search_note`;

  try {
    await chrome.tabs.update(tabId, { url: searchUrl });
    await waitForTabComplete(tabId, null, 20000);
    await sleep(1800);  // 等待 Vue 渲染完成

    const results = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: (nid) => {
        // 方法 1：__INITIAL_STATE__ 搜索结果
        try {
          const s = window.__INITIAL_STATE__;
          const pool = [
            ...(s?.search?.noteResults  || []),
            ...(s?.search?.items        || []),
            ...(s?.searchResult?.items  || []),
          ];
          for (const item of pool) {
            const id    = item.id || item.noteId || item.note?.noteId;
            const token = item.xsecToken || item.note?.xsecToken;
            if (id === nid && token) return token;
          }
        } catch (_) {}

        // 方法 2：DOM 链接
        const links = document.querySelectorAll(`a[href*="${nid}"]`);
        for (const link of links) {
          const href = link.href || link.getAttribute("href") || "";
          const m = href.match(/xsec_token=([^&]+)/);
          if (m) return decodeURIComponent(m[1]);
        }
        return null;
      },
      args: [noteId],
    });

    return results?.[0]?.result || null;
  } catch (_) {
    return null;
  }
}

async function cmdNavigate({ url, _retried = false }) {
  const tab = await getOrOpenXhsTab();

  // 若当前已在 xiaohongshu.com，用页面内 window.location.href 发起导航。
  // 这样浏览器将导航标记为 same-origin（Sec-Fetch-Site: same-origin），
  // 与真实用户点击链接行为一致，避免 chrome.tabs.update 产生的
  // Sec-Fetch-Site: none/cross-site 被 XHS 服务端识别为自动化访问。
  const isOnXhs = tab.url && tab.url.includes("xiaohongshu.com");
  if (isOnXhs) {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: (targetUrl) => { window.location.href = targetUrl; },
      args: [url],
    });
  } else {
    await chrome.tabs.update(tab.id, { url });
  }

  await waitForTabComplete(tab.id, url, 60000);

  // ── 最终落地 URL 检测：XHS 风控 302 重定向到 /404?source=原路径 ──
  const finalTab = await chrome.tabs.get(tab.id).catch(() => null);
  const finalUrl = finalTab?.url || "";
  if (/xiaohongshu\.com\/404(\?|$)/.test(finalUrl)) {
    // 优先使用 webRequest 观测器已存储的诊断（捕获的是第一跳 302，最准确）
    // webRequest 事件写入是异步的，稍等一下确保已落盘
    await sleep(400);
    let event = null;
    const stored = await chrome.storage.session.get("blockEvents").catch(() => ({}));
    // 用时间窗口（60s）+ URL 包含关系匹配，避免 query 参数顺序差异导致精确匹配失败
    const nowMs = Date.now();
    const recent = (stored.blockEvents || []).find(
      e => e.intercept_type === "webRequest_302" &&
           e.url && url.includes(e.url.split("?")[0].split("/").pop()) &&
           (nowMs - new Date(e.timestamp).getTime()) < 60000,
    );
    if (recent) {
      event = recent;
    } else {
      // fallback：DOM 分析（落地在 /404 页面，准确度较低）
      event = await detectNavigationBlock(tab.id, url, finalUrl).catch(() => null);
    }

    const cause = event?.diagnosis?.cause_category;
    const errorCode = event?.trigger?.primary?.match(/error_code=(\d+)/)?.[1] || null;

    // ── Auto-fix：token 过期/缺失时，从搜索页取新 token 重试 ──
    //
    // 只在以下情况尝试：
    //   1. 没有重试过（_retried=false）
    //   2. 原因是 token 类问题
    //   3. error_code 是 300032（过期）或没有 error_code（参数缺失）
    //      300031 = token 签名错误（可能是内容下架伪装），重试无意义
    //      300033 = IP 绑定不匹配，换 token 有意义
    const tokenExpiredOrMissing = !errorCode || errorCode === "300032" || errorCode === "300033";
    if (!_retried && cause === "token" && tokenExpiredOrMissing) {
      const noteId = extractNoteId(url);
      if (noteId) {
        console.log(`[XHS Bridge] token 过期/缺失（error_code=${errorCode || "无"}），搜索页取新 token（noteId=${noteId}）...`);
        const freshToken = await getFreshXsecToken(tab.id, noteId).catch(() => null);
        if (freshToken) {
          const u = new URL(url);
          u.searchParams.set("xsec_token", freshToken);
          u.searchParams.set("xsec_source", "pc_search");
          const fixedUrl = u.toString();
          console.log(`[XHS Bridge] 获取新 token 成功，重新导航: ${fixedUrl}`);
          return cmdNavigate({ url: fixedUrl, _retried: true });
        }
        console.log("[XHS Bridge] 未能从搜索页获取新 token，内容可能已被删除");
      }
    }
    // 300031 且已有有效 token = 内容本身不可访问（删除/下架/区域限制）
    if (errorCode === "300031" && event?.url_params?.xsec_token) {
      const rootCause2 = event.diagnosis.root_cause;
      throw new Error(`笔记内容不可访问（已删除/下架/区域限制），服务端以 token 错误掩盖真实原因：${rootCause2}`);
    }

    const rootCause = event?.diagnosis?.root_cause || "未知原因";
    throw new Error(`笔记被风控拦截（${rootCause}），重定向至: ${finalUrl}`);
  }

  // ── 页面渲染级 404 检测（HTTP 200 但 SPA 渲染出错误页）──
  await detectNavigationBlock(tab.id, url, finalUrl).catch(() => {});

  // 注入 visibilityState 伪装：XHS 监听页面可见性，后台标签页会暂停 Vue 渲染。
  // 覆盖后页面始终认为自己在前台，允许在后台标签页正常提取数据。
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: () => {
      try {
        Object.defineProperty(document, "visibilityState", {
          get: () => "visible",
          configurable: true,
        });
        Object.defineProperty(document, "hidden", {
          get: () => false,
          configurable: true,
        });
        document.dispatchEvent(new Event("visibilitychange"));
      } catch (_) {}
    },
  }).catch(() => {});

  return null;
}

async function cmdWaitForLoad({ timeout = 60000 }) {
  const tab = await getOrOpenXhsTab();
  await waitForTabComplete(tab.id, null, timeout);
  return null;
}

async function waitForTabComplete(tabId, expectedUrlPrefix, timeout) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + timeout;

    function listener(id, info, updatedTab) {
      if (id !== tabId) return;
      if (info.status !== "complete") return;
      if (expectedUrlPrefix && !updatedTab.url?.startsWith(expectedUrlPrefix.slice(0, 20))) return;
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }

    chrome.tabs.onUpdated.addListener(listener);

    // 轮询兜底：若事件在监听前已触发
    const poll = async () => {
      if (Date.now() > deadline) {
        chrome.tabs.onUpdated.removeListener(listener);
        reject(new Error("页面加载超时"));
        return;
      }
      const tab = await chrome.tabs.get(tabId).catch(() => null);
      if (tab && tab.status === "complete") {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
        return;
      }
      setTimeout(poll, 400);
    };
    setTimeout(poll, 600);
  });
}

// ───────────────────────── 截图 ─────────────────────────

async function cmdScreenshot() {
  const tab = await getOrOpenXhsTab();
  const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
  return { data: dataUrl.split(",")[1] };
}

// ───────────────────────── Cookies ─────────────────────────

async function cmdGetCookies({ domain = "xiaohongshu.com" }) {
  return await chrome.cookies.getAll({ domain });
}

// ───────────────────────── MAIN world JS 执行 ─────────────────────────

async function cmdEvaluateInMainWorld(method, params) {
  const tab = await getOrOpenXhsTab();
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: mainWorldExecutor,
    args: [method, params],
  });
  const r = results?.[0]?.result;
  if (r && typeof r === "object" && "__xhs_error" in r) {
    throw new Error(r.__xhs_error);
  }
  return r;
}

/**
 * 在页面主 world 运行，可访问 window.__INITIAL_STATE__ 等页面全局变量。
 * 注意：此函数被序列化后注入页面，不能引用外部变量。
 */
function mainWorldExecutor(method, params) {
  function poll(check, interval, timeout) {
    return new Promise((resolve, reject) => {
      const start = Date.now();
      (function tick() {
        const result = check();
        if (result !== false && result !== null && result !== undefined) {
          resolve(result);
          return;
        }
        if (Date.now() - start >= timeout) {
          reject(new Error("超时"));
          return;
        }
        setTimeout(tick, interval);
      })();
    });
  }

  switch (method) {
    case "evaluate": {
      try {
        // eslint-disable-next-line no-new-func
        return Function(`"use strict"; return (${params.expression})`)();
      } catch (e) {
        return { __xhs_error: `JS执行错误: ${e.message}` };
      }
    }

    case "has_element":
      return document.querySelector(params.selector) !== null;

    case "get_elements_count":
      return document.querySelectorAll(params.selector).length;

    case "get_element_text": {
      const el = document.querySelector(params.selector);
      return el ? el.textContent.trim() : null;
    }

    case "get_elements_info": {
      return Array.from(document.querySelectorAll(params.selector)).map(el => {
        const info = { text: el.textContent.trim() };
        if (params.attrs) for (const a of params.attrs) info[a] = el.getAttribute(a);
        return info;
      });
    }

    case "get_element_attribute": {
      const el = document.querySelector(params.selector);
      return el ? el.getAttribute(params.attr) : null;
    }

    case "get_scroll_top":
      return window.pageYOffset || document.documentElement.scrollTop || 0;

    case "get_viewport_height":
      return window.innerHeight;

    case "get_url":
      return window.location.href;

    case "wait_dom_stable": {
      const timeout = params.timeout || 10000;
      const interval = params.interval || 500;
      return new Promise((resolve) => {
        let last = -1;
        const start = Date.now();
        (function tick() {
          const size = document.body ? document.body.innerHTML.length : 0;
          if (size === last && size > 0) { resolve(null); return; }
          last = size;
          if (Date.now() - start >= timeout) { resolve(null); return; }
          setTimeout(tick, interval);
        })();
      });
    }

    case "wait_for_selector": {
      const timeout = params.timeout || 30000;
      return poll(
        () => document.querySelector(params.selector) ? true : false,
        200,
        timeout,
      ).catch(() => { throw new Error(`等待元素超时: ${params.selector}`); });
    }

    case "get_iframe_text": {
      return new Promise(resolve => {
        const iframe = document.querySelector(params.iframe_selector);
        if (!iframe) { resolve(null); return; }
        function tryRead() {
          try {
            const doc = iframe.contentDocument || iframe.contentWindow?.document;
            if (!doc || doc.readyState !== "complete") return false;
            const spans = doc.querySelectorAll(params.text_selector || ".textLayer span");
            if (spans.length === 0) return false;
            return Array.from(spans).map(s => s.textContent).join(" ");
          } catch (e) {
            return { __xhs_error: `iframe 访问失败: ${e.message}` };
          }
        }
        const timeout = params.timeout || 15000;
        const start = Date.now();
        (function tick() {
          const result = tryRead();
          if (result && result !== false) { resolve(result); return; }
          if (result?.__xhs_error) { resolve(result); return; }
          if (Date.now() - start >= timeout) { resolve(null); return; }
          setTimeout(tick, 300);
        })();
      });
    }

    default:
      return { __xhs_error: `未知 MAIN world 方法: ${method}` };
  }
}

// ───────────────────────── 工具函数 ──────────────────────────────────

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ───────────────────────── 真实鼠标点击（chrome.debugger + CDP） ──────

// 在视口绝对坐标 (x, y) 派发真实鼠标事件序列：mouseMoved（轨迹）+ mousePressed + mouseReleased。
// 真事件走渲染层，能穿透 closed shadow DOM；JS 合成事件做不到。
async function _dispatchRealClickAt(target, x, y) {
  const startX = x - 20;
  const startY = y - 45;
  const steps = 5;
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mouseMoved",
      x: Math.round(startX + (x - startX) * t),
      y: Math.round(startY + (y - startY) * t),
      button: "none", buttons: 0, modifiers: 0,
    });
    await sleep(8);
  }

  const base = { x, y, button: "left", buttons: 1, clickCount: 1, modifiers: 0 };
  await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", { ...base, type: "mousePressed" });
  await sleep(30);
  await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", { ...base, type: "mouseReleased", buttons: 0 });
}

async function cmdClickViaDebugger(method, { selector, index, text }) {
  const tab = await getOrOpenXhsTab();
  const target = { tabId: tab.id };

  // 按调用方式构造元素查找表达式
  let findExpr;
  if (method === "click_nth_element") {
    findExpr = `document.querySelectorAll(${JSON.stringify(selector)})[${index}] || null`;
  } else if (method === "click_element_by_text") {
    findExpr = `Array.from(document.querySelectorAll(${JSON.stringify(selector)})).find(e => e.textContent.includes(${JSON.stringify(text)})) || null`;
  } else {
    findExpr = `document.querySelector(${JSON.stringify(selector)})`;
  }

  // 防御性 detach：清掉残留 attach 状态
  await chrome.debugger.detach(target).catch(() => {});
  await chrome.debugger.attach(target, "1.3");
  try {
    // 先滚动元素到视口中心，再取坐标
    const evalResult = await chrome.debugger.sendCommand(target, "Runtime.evaluate", {
      expression: `(() => {
        const el = ${findExpr};
        if (!el) return null;
        el.scrollIntoView({ block: "center", behavior: "instant" });
        const r = el.getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
      })()`,
      returnByValue: true,
    });

    const pos = evalResult?.result?.value;
    if (!pos) throw new Error(`元素不存在: ${JSON.stringify({ selector, index, text })}`);

    await _dispatchRealClickAt(target, pos.x, pos.y);
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
  return null;
}

// ─────── 真实键盘（chrome.debugger + CDP Input.dispatchKeyEvent） ──────

async function cmdPressKeyViaDebugger({ key }) {
  const tab = await getOrOpenXhsTab();

  // 检查当前焦点是否在 contenteditable
  const ceResult = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: () => document.activeElement?.isContentEditable ?? false,
  });
  const inCE = ceResult?.[0]?.result;

  if (inCE) {
    // contenteditable: execCommand 产生 isTrusted input 事件
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: (k) => {
        if (k === "Enter") {
          document.execCommand("insertParagraph", false, null);
        } else if (k === "ArrowDown") {
          const active = document.activeElement;
          const sel = window.getSelection();
          if (sel && active.childNodes.length) {
            sel.selectAllChildren(active);
            sel.collapseToEnd();
          }
        } else if (k === "Backspace") {
          document.execCommand("delete", false, null);
        }
      },
      args: [key],
    });
    return null;
  }

  // 非 contenteditable: debugger Input.dispatchKeyEvent（isTrusted: true）
  const KEY_MAP = {
    Enter:      { key: "Enter",      code: "Enter",      windowsVirtualKeyCode: 13 },
    Tab:        { key: "Tab",        code: "Tab",        windowsVirtualKeyCode: 9  },
    Backspace:  { key: "Backspace",  code: "Backspace",  windowsVirtualKeyCode: 8  },
    Delete:     { key: "Delete",     code: "Delete",     windowsVirtualKeyCode: 46 },
    Escape:     { key: "Escape",     code: "Escape",     windowsVirtualKeyCode: 27 },
    ArrowDown:  { key: "ArrowDown",  code: "ArrowDown",  windowsVirtualKeyCode: 40 },
    ArrowUp:    { key: "ArrowUp",    code: "ArrowUp",    windowsVirtualKeyCode: 38 },
    ArrowLeft:  { key: "ArrowLeft",  code: "ArrowLeft",  windowsVirtualKeyCode: 37 },
    ArrowRight: { key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39 },
    Space:      { key: " ",          code: "Space",      windowsVirtualKeyCode: 32 },
  };
  const info = KEY_MAP[key] || { key, code: `Key${key.toUpperCase()}`, windowsVirtualKeyCode: key.charCodeAt(0) };

  const target = { tabId: tab.id };
  await chrome.debugger.attach(target, "1.3");
  try {
    const base = { modifiers: 0, ...info };
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", { ...base, type: "keyDown" });
    await sleep(30);
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", { ...base, type: "keyUp" });
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
  return null;
}

// ─────── 真实文字输入（chrome.debugger + CDP Input.insertText） ──────

async function cmdTypeTextViaDebugger({ text, delayMs = 50 }) {
  const tab = await getOrOpenXhsTab();

  // 检查当前焦点是否在 contenteditable
  const ceResult = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: () => document.activeElement?.isContentEditable ?? false,
  });
  const inCE = ceResult?.[0]?.result;

  if (inCE) {
    // contenteditable: 逐字 execCommand("insertText")，单次 executeScript 避免 IPC 开销
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: async (chars, delay) => {
        function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
        for (const char of chars) {
          document.execCommand("insertText", false, char);
          await sleep(delay);
        }
      },
      args: [[...text], delayMs],
    });
    return null;
  }

  // 非 contenteditable: 逐字 Input.insertText（isTrusted: true）
  const target = { tabId: tab.id };
  await chrome.debugger.attach(target, "1.3");
  try {
    for (const char of text) {
      await chrome.debugger.sendCommand(target, "Input.insertText", { text: char });
      await sleep(delayMs);
    }
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
  return null;
}

// ───────────────────────── 文件上传（chrome.debugger + CDP） ─────────

async function cmdSetFileInputViaDebugger({ selector, files }) {
  const tab = await getOrOpenXhsTab();
  const target = { tabId: tab.id };

  await chrome.debugger.attach(target, "1.3");
  try {
    const { root } = await chrome.debugger.sendCommand(target, "DOM.getDocument", { depth: 0 });
    const { nodeId } = await chrome.debugger.sendCommand(target, "DOM.querySelector", {
      nodeId: root.nodeId,
      selector,
    });
    if (!nodeId) throw new Error(`文件输入框不存在: ${selector}`);
    await chrome.debugger.sendCommand(target, "DOM.setFileInputFiles", {
      nodeId,
      files,  // 本地文件路径数组，由 Python 侧提供
    });
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
  return null;
}

// ───────────────────────── DOM 操作（MAIN world） ────────────────────

async function cmdDomInMainWorld(method, params) {
  const tab = await getOrOpenXhsTab();
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: domExecutor,
    args: [method, params],
  });
  const r = results?.[0]?.result;
  if (r && typeof r === "object" && "__xhs_error" in r) {
    throw new Error(r.__xhs_error);
  }
  return r ?? null;
}

/**
 * DOM 操作执行器，在页面 MAIN world 运行。
 * 不能引用外部变量，所有逻辑自包含。
 */
function domExecutor(method, params) {
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  function requireEl(selector) {
    const el = document.querySelector(selector);
    if (!el) return { __xhs_error: `元素不存在: ${selector}` };
    return el;
  }

  switch (method) {
    case "input_text": {
      const el = requireEl(params.selector);
      if (el.__xhs_error) return el;
      el.focus();
      el.value = params.text;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return null;
    }

    case "input_content_editable": {
      return new Promise(async (resolve) => {
        const el = document.querySelector(params.selector);
        if (!el) { resolve({ __xhs_error: `元素不存在: ${params.selector}` }); return; }
        el.focus();
        document.execCommand("selectAll", false, null);
        document.execCommand("delete", false, null);
        await sleep(80);
        const lines = params.text.split("\n");
        for (let i = 0; i < lines.length; i++) {
          if (lines[i]) document.execCommand("insertText", false, lines[i]);
          if (i < lines.length - 1) {
            // insertParagraph 才能在 contenteditable 里真正插入换行
            document.execCommand("insertParagraph", false, null);
            await sleep(30);
          }
        }
        resolve(null);
      });
    }

    case "set_file_input": {
      return new Promise((resolve) => {
        const el = document.querySelector(params.selector);
        if (!el) { resolve({ __xhs_error: `文件输入框不存在: ${params.selector}` }); return; }

        function makeFiles() {
          const dt = new DataTransfer();
          for (const f of params.files) {
            const bytes = Uint8Array.from(atob(f.data), c => c.charCodeAt(0));
            dt.items.add(new File([bytes], f.name, { type: f.type }));
          }
          return dt;
        }

        // 方法1: 覆盖 files 属性 + change 事件（标准 file input）
        try {
          const dt = makeFiles();
          Object.defineProperty(el, "files", { value: dt.files, configurable: true, writable: true });
          el.dispatchEvent(new Event("change", { bubbles: true }));
          el.dispatchEvent(new Event("input", { bubbles: true }));
        } catch (e) {}

        // 方法2: drag-drop 到上传区域（XHS 主要监听 drop 事件）
        const dropTarget =
          el.closest('[class*="upload"]') ||
          el.closest('[class*="Upload"]') ||
          el.parentElement;
        if (dropTarget) {
          try {
            const dt2 = makeFiles();
            dropTarget.dispatchEvent(new DragEvent("dragenter", { bubbles: true, cancelable: true, dataTransfer: dt2 }));
            dropTarget.dispatchEvent(new DragEvent("dragover",  { bubbles: true, cancelable: true, dataTransfer: dt2 }));
            dropTarget.dispatchEvent(new DragEvent("drop",      { bubbles: true, cancelable: true, dataTransfer: dt2 }));
          } catch (e) {}
        }

        resolve(null);
      });
    }

    case "scroll_by":
      window.scrollBy(params.x || 0, params.y || 0); return null;
    case "scroll_to":
      window.scrollTo(params.x || 0, params.y || 0); return null;
    case "scroll_to_bottom":
      window.scrollTo(0, document.body.scrollHeight); return null;

    case "scroll_element_into_view": {
      const el = document.querySelector(params.selector);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      return null;
    }
    case "scroll_nth_element_into_view": {
      const els = document.querySelectorAll(params.selector);
      if (els[params.index]) els[params.index].scrollIntoView({ behavior: "smooth", block: "center" });
      return null;
    }

    case "dispatch_wheel_event": {
      const target = document.querySelector(".note-scroller") ||
        document.querySelector(".interaction-container") || document.documentElement;
      target.dispatchEvent(new WheelEvent("wheel", { deltaY: params.deltaY || 0, deltaMode: 0, bubbles: true, cancelable: true }));
      return null;
    }

    case "mouse_move":
      document.dispatchEvent(new MouseEvent("mousemove", { clientX: params.x, clientY: params.y, bubbles: true }));
      return null;

    case "mouse_click": {
      const el = document.elementFromPoint(params.x, params.y);
      if (el) {
        ["mousedown", "mouseup", "click"].forEach(t =>
          el.dispatchEvent(new MouseEvent(t, { clientX: params.x, clientY: params.y, bubbles: true }))
        );
      }
      return null;
    }

    case "remove_element": {
      const el = document.querySelector(params.selector);
      if (el) el.remove();
      return null;
    }

    case "hover_element": {
      const el = document.querySelector(params.selector);
      if (el) {
        const rect = el.getBoundingClientRect();
        const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
        el.dispatchEvent(new MouseEvent("mouseover", { clientX: x, clientY: y, bubbles: true }));
        el.dispatchEvent(new MouseEvent("mousemove", { clientX: x, clientY: y, bubbles: true }));
      }
      return null;
    }

    case "select_all_text": {
      const el = document.querySelector(params.selector);
      if (el) { el.focus(); if (el.select) el.select(); else document.execCommand("selectAll"); }
      return null;
    }

    default:
      return { __xhs_error: `未知 DOM 命令: ${method}` };
  }
}

// ─── 302→/404 真实触发机制观测器 ─────────────────────────────────────────────
//
// 使用 webRequest API，在浏览器跟随 302 之前捕获原始 HTTP 上下文。
// 这是唯一能看到服务端判断依据的方法：
//   Phase 1  onBeforeSendHeaders — 记录发出去的请求头（含 Cookie、Sec-Fetch-* 等）
//   Phase 2  onHeadersReceived   — 检测 302 响应，Location → /404 时触发分析
//
// 两阶段用 requestId 关联，因此能完整重建"XHS 收到什么请求 → 决定 302"的因果链。

const _reqCache = new Map();  // requestId → 请求上下文（Phase 1 填充）

chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    // 只缓存页面级导航（main_frame）和 XHR/fetch，跳过图片/字体等资源
    if (!["main_frame", "sub_frame", "xmlhttprequest", "fetch"].includes(details.type)) return;

    const reqHeaders = {};
    for (const h of details.requestHeaders || []) {
      reqHeaders[h.name.toLowerCase()] = h.value;
    }

    // 限制 Map 大小，防止 service worker 内存泄漏
    if (_reqCache.size > 200) {
      const firstKey = _reqCache.keys().next().value;
      _reqCache.delete(firstKey);
    }

    _reqCache.set(details.requestId, {
      url:            details.url,
      method:         details.method,
      type:           details.type,
      tabId:          details.tabId,
      ts:             Date.now(),
      requestHeaders: reqHeaders,
    });
  },
  {
    urls: ["https://www.xiaohongshu.com/*", "https://xiaohongshu.com/*"],
  },
  ["requestHeaders", "extraHeaders"],
);

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    const reqCtx = _reqCache.get(details.requestId);
    // Phase 2 完成后清理缓存（无论是否 302）
    _reqCache.delete(details.requestId);

    if (details.statusCode !== 301 && details.statusCode !== 302) return;

    // 跳过 /404 路径本身的二次跳转（只捕获第一跳：内容 URL → /404）
    // XHS 的重定向链：/explore/{id} → /404/sec_xxx → /404?source=...
    // 我们只要第一跳，它包含真实的 error_code 和 token 状态
    if (/\/404(\/|$|\?)/.test(details.url)) return;

    // 找 Location 响应头
    const locationHeader = (details.responseHeaders || [])
      .find(h => h.name.toLowerCase() === "location");
    const location = locationHeader?.value || "";

    // 只关心重定向到 /404 的（XHS 风控标志）
    if (!/\/404(\/|$|\?)/.test(location)) return;

    // ── 解析请求 URL 关键参数 ──────────────────────────────────
    let urlParams = { path: "", xsec_token: null, xsec_source: null };
    try {
      const u = new URL(details.url);
      urlParams.path         = u.pathname;
      urlParams.xsec_token   = u.searchParams.get("xsec_token");
      urlParams.xsec_source  = u.searchParams.get("xsec_source");
    } catch (_) {}

    // ── 解析请求 Cookie（从请求头中提取） ─────────────────────
    const cookieStr = reqCtx?.requestHeaders?.["cookie"] || "";
    const cookieMap = {};
    for (const p of cookieStr.split(";")) {
      const i = p.indexOf("=");
      if (i < 0) continue;
      cookieMap[p.slice(0, i).trim()] = p.slice(i + 1).trim();
    }
    const cookieAnalysis = {
      has_a1:              "a1" in cookieMap,
      has_web_session:     "web_session" in cookieMap,
      has_webId:           "webId" in cookieMap,
      a1_preview:          cookieMap["a1"]          ? cookieMap["a1"].slice(0, 12)          + "…" : null,
      web_session_preview: cookieMap["web_session"] ? cookieMap["web_session"].slice(0, 10) + "…" : null,
    };

    // ── 收集 302 响应头 ────────────────────────────────────────
    const respHeaders = {};
    for (const h of details.responseHeaders || []) {
      respHeaders[h.name.toLowerCase()] = h.value;
    }

    // ── 触发机制判断（基于实测 HTTP 数据） ─────────────────────
    //
    // 观测到 302 时，通过请求头和 URL 参数推断 XHS 服务端做了什么判断：
    //
    //  A. Cookie 无 web_session   → 服务端无法解密 token，路由层立即 302
    //  B. URL 无 xsec_token       → 路由层参数校验失败，立即 302（早于业务逻辑）
    //  C. 两者都有但仍 302         → token 解密成功但内容验证失败
    //                               （ipHash / ts / source / userId 不匹配）
    //
    // Sec-Fetch-Site 是额外的观测维度：
    //  "none"       = 直接导航（地址栏/bookmark），是自动化的强信号
    //  "same-origin" = 从 XHS 页面内部点击/跳转，最安全
    //  "cross-site"  = 从外部页面跳来（不可信）

    let triggerPrimary, triggerEvidence, causeCategory;
    const secFetchSite = reqCtx?.requestHeaders?.["sec-fetch-site"];

    // 从 302 Location 里解析 XHS 服务端给出的 error_code
    // 格式：/404/sec_xxx?redirectPath=...&error_code=300031&error_msg=...
    let errorCode = null, errorMsg = null;
    try {
      const locUrl = new URL(location, "https://www.xiaohongshu.com");
      errorCode = locUrl.searchParams.get("error_code");
      errorMsg  = decodeURIComponent(locUrl.searchParams.get("error_msg") || "");
    } catch (_) {}

    // error_code 语义（实测）：
    //   300031 = token 签名验证失败（格式错误 / 签名不匹配）
    //   300032 = token 过期（ts 字段超有效期）
    //   300033 = token 与 session/IP 绑定不匹配
    //   无 error_code = 路由层参数缺失（最外层，token 不存在）
    const ERROR_CODE_MEANING = {
      "300031": "token 签名验证失败（格式/签名错误）",
      "300032": "token 已过期",
      "300033": "token 与当前 session / IP 绑定不匹配",
    };

    if (!cookieAnalysis.has_web_session) {
      triggerPrimary  = "web_session 不存在 → 服务端无法鉴权，302";
      triggerEvidence = "Cookie 头中无 web_session 字段，token 解密密钥缺失";
      causeCategory   = "session";
    } else if (!urlParams.xsec_token) {
      triggerPrimary  = "URL 缺少 xsec_token 参数 → 路由层参数校验失败，302";
      triggerEvidence = `路径 ${urlParams.path} 无 xsec_token 查询参数`;
      causeCategory   = "token";
    } else if (errorCode) {
      const meaning   = ERROR_CODE_MEANING[errorCode] || `未知错误码 ${errorCode}`;
      triggerPrimary  = `xsec_token 验证失败（error_code=${errorCode}）→ ${meaning}`;
      triggerEvidence =
        `token 前缀=${urlParams.xsec_token.slice(0, 20)}… source=${urlParams.xsec_source || "无"}` +
        `，error_msg="${errorMsg}"，Sec-Fetch-Site=${secFetchSite || "无"}`;
      causeCategory   = "token";
    } else {
      triggerPrimary  = "xsec_token 内容验证失败 → 服务端解密后字段不匹配，302";
      triggerEvidence =
        `token 前缀=${urlParams.xsec_token.slice(0, 20)}… source=${urlParams.xsec_source || "无"}` +
        `，Sec-Fetch-Site=${secFetchSite || "无"}`;
      causeCategory   = "token";
    }

    const event = {
      id:             `${Date.now()}_302webreq`,
      timestamp:      new Date().toISOString(),
      intercept_type: "webRequest_302",          // 区分于 fetch/XHR 拦截

      // ── 请求上下文（Phase 1 采集）──
      url:            details.url,
      method:         details.method,
      resource_type:  reqCtx?.type || details.type,
      redirect_to:    location,

      url_params: urlParams,

      // 自动化指纹相关请求头（XHS 服务端可以看到这些）
      request_fingerprint: {
        "sec-fetch-site":   secFetchSite,
        "sec-fetch-mode":   reqCtx?.requestHeaders?.["sec-fetch-mode"],
        "sec-fetch-dest":   reqCtx?.requestHeaders?.["sec-fetch-dest"],
        "referer":          reqCtx?.requestHeaders?.["referer"],
        "origin":           reqCtx?.requestHeaders?.["origin"],
        "user-agent":       reqCtx?.requestHeaders?.["user-agent"]?.slice(0, 100),
      },

      cookies: cookieAnalysis,

      // ── 302 响应头（Phase 2 采集）—— 服务端返回的原始信号 ──
      response_302_headers: {
        location:           respHeaders["location"],
        "cache-control":    respHeaders["cache-control"],
        "x-request-id":     respHeaders["x-request-id"],
        "set-cookie":       respHeaders["set-cookie"],
        server:             respHeaders["server"],
      },

      // ── 触发机制分析 ──
      trigger: {
        primary:            triggerPrimary,
        evidence:           triggerEvidence,
        sec_fetch_site:     secFetchSite,
        is_direct_nav:      secFetchSite === "none",  // 地址栏/自动化直接导航的特征
      },

      // 兼容现有诊断格式
      diagnosis: {
        root_cause:      triggerPrimary,
        cause_category:  causeCategory,
        detail:          triggerEvidence,
        how_xhs_decides:
          "webRequest 层实测捕获。" +
          "XHS 在 CDN/路由层校验 xsec_token 参数存在性（最外层）；" +
          "通过后进入签名层用 web_session 解密 token 内容；" +
          "最后核对 {noteId, userId, ipHash, ts, source} 各字段。",
      },
    };

    storeBlockEvent(event).catch(() => {});
    chrome.runtime.sendMessage({ type: "BLOCK_EVENT_ADDED", event }).catch(() => {});
    console.log(
      `[XHS 302观测] ${triggerPrimary}\n` +
      `  URL: ${details.url.slice(0, 100)}\n` +
      `  Sec-Fetch-Site: ${secFetchSite || "无"} | xsec_token: ${urlParams.xsec_token ? "有" : "无"}`,
    );
  },
  {
    urls: ["https://www.xiaohongshu.com/*", "https://xiaohongshu.com/*"],
  },
  ["responseHeaders", "extraHeaders"],
);

// 非 302 请求完成时清理缓存
chrome.webRequest.onCompleted.addListener(
  (d) => _reqCache.delete(d.requestId),
  { urls: ["https://www.xiaohongshu.com/*", "https://xiaohongshu.com/*"] },
);
chrome.webRequest.onErrorOccurred.addListener(
  (d) => _reqCache.delete(d.requestId),
  { urls: ["https://www.xiaohongshu.com/*", "https://xiaohongshu.com/*"] },
);

// ───────────────────────── 404 诊断事件存储 ──────────────────

const MAX_BLOCK_EVENTS = 50;

async function storeBlockEvent(event) {
  const data = await chrome.storage.session.get("blockEvents");
  const events = data.blockEvents || [];
  events.unshift(event);                        // 最新的在前
  if (events.length > MAX_BLOCK_EVENTS) events.length = MAX_BLOCK_EVENTS;
  await chrome.storage.session.set({ blockEvents: events });
}

// ───────────────────────── 风控分析 ─────────────────────────

async function cmdAnalyzeRiskControl({ probeUrls = [] } = {}) {
  const tab = await getOrOpenXhsTab();
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: riskControlAnalyzer,
    args: [probeUrls],
  });
  return results?.[0]?.result ?? null;
}

/**
 * 在页面 MAIN world 执行风控分析。
 * 必须完全自包含，不可引用任何外部变量。
 */
async function riskControlAnalyzer(extraProbeUrls) {
  const report = {
    timestamp: new Date().toISOString(),
    fingerprints: {},
    page_state: {},
    api_probes: {},
    risk_level: "safe",
    issues: [],
  };

  // ── 1. 自动化指纹检测 ─────────────────────────────────────────
  const fp = report.fingerprints;

  fp.webdriver = navigator.webdriver;
  fp.plugins_count = navigator.plugins.length;
  fp.languages = Array.from(navigator.languages || []);
  fp.user_agent = navigator.userAgent;
  fp.is_headless_ua = /HeadlessChrome|Electron\/|PhantomJS/.test(navigator.userAgent);
  fp.outer_width = window.outerWidth;
  fp.outer_height = window.outerHeight;
  fp.screen_width = screen.width;
  fp.screen_height = screen.height;
  fp.color_depth = screen.colorDepth;
  fp.device_memory = navigator.deviceMemory;
  fp.hardware_concurrency = navigator.hardwareConcurrency;
  fp.platform = navigator.platform;
  fp.chrome_exists = typeof window.chrome !== "undefined";
  fp.chrome_runtime = !!(window.chrome && window.chrome.runtime);
  fp.visibility_state = document.visibilityState;
  fp.document_hidden = document.hidden;

  // 权限探测（无头浏览器 notifications 默认 denied）
  try {
    const perm = await navigator.permissions.query({ name: "notifications" });
    fp.notifications_permission = perm.state;
  } catch (_) {
    fp.notifications_permission = "error";
  }

  // WebGL 软件渲染检测（SwiftShader = 无头特征）
  try {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl") || canvas.getContext("experimental-webgl");
    if (gl) {
      const dbg = gl.getExtension("WEBGL_debug_renderer_info");
      if (dbg) {
        fp.webgl_vendor   = gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL);
        fp.webgl_renderer = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL);
        fp.webgl_is_swiftshader = /SwiftShader|llvmpipe|softpipe/i.test(fp.webgl_renderer || "");
      }
    }
  } catch (_) {}

  // ── 2. 页面状态 / XHS 专项检测 ───────────────────────────────
  const ps = report.page_state;

  ps.current_url = window.location.href;
  ps.page_title  = document.title;

  try {
    const state = window.__INITIAL_STATE__;
    if (state) {
      ps.has_initial_state = true;
      ps.user_logged_in    = !!(state?.user?.userInfo?.userId || state?.login?.userInfo?.userId);
      const riskKeys = ["riskControl", "blockReason", "needVerification", "forbidden"];
      ps.state_risk_keys   = riskKeys.filter(k => k in state);
    } else {
      ps.has_initial_state = false;
    }
  } catch (_) {
    ps.has_initial_state = false;
  }

  ps.has_captcha_modal  = !!(
    document.querySelector('[class*="captcha"]') ||
    document.querySelector('[class*="verify-modal"]') ||
    document.querySelector("#captcha-container")
  );
  ps.page_is_404        = document.title.includes("404") ||
    !!(document.querySelector('[class*="not-found"]') || document.querySelector('[class*="error-page"]'));
  ps.page_has_risk_block = !!(
    document.querySelector('[class*="risk-block"]') ||
    document.querySelector('[class*="forbidden-page"]')
  );

  // 扫描当前页面已有的 API 请求失败记录（Chrome 109+ 支持 responseStatus）
  const resFailed = [];
  for (const r of performance.getEntriesByType("resource")) {
    if (r.name.includes("xiaohongshu.com") && r.name.includes("/api/")) {
      const s = r.responseStatus;
      if (s && (s === 404 || s === 461 || s === 403 || s === 999)) {
        resFailed.push({ url: r.name, status: s });
      }
    }
  }
  ps.api_failures_on_page = resFailed;

  // ── 3. 实时 API 探测 ─────────────────────────────────────────
  const defaultProbes = [
    {
      key: "/api/sns/web/v1/search/complete",
      url: "https://www.xiaohongshu.com/api/sns/web/v1/search/complete?keyword=test",
      method: "GET",
    },
    {
      key: "/api/sns/web/v1/homefeed",
      url: "https://www.xiaohongshu.com/api/sns/web/v1/homefeed",
      method: "POST",
      body: JSON.stringify({ cursor_score: "", num: 1, refresh_type: 1, note_index: 1 }),
    },
    {
      key: "/api/sns/web/v2/user/me",
      url: "https://www.xiaohongshu.com/api/sns/web/v2/user/me",
      method: "GET",
    },
  ];

  const probes = [
    ...defaultProbes,
    ...extraProbeUrls.map(u => ({ key: u, url: u, method: "GET" })),
  ];

  for (const probe of probes) {
    try {
      const opts = { method: probe.method, credentials: "include" };
      if (probe.body) {
        opts.body = probe.body;
        opts.headers = { "Content-Type": "application/json" };
      }
      const resp = await fetch(probe.url, opts);
      let body = null;
      try { body = await resp.json(); } catch (_) {}
      report.api_probes[probe.key] = {
        status: resp.status,
        ok: resp.ok,
        xhs_code: body?.code ?? null,
        xhs_msg:  body?.msg  || body?.message || null,
        // 404/461(XHS风控)/999(系统封锁) 均为被拦截信号
        risk_blocked: resp.status === 404 || resp.status === 461 || resp.status === 999,
      };
    } catch (e) {
      report.api_probes[probe.key] = { error: String(e.message || e) };
    }
  }

  // ── 4. 汇总风险等级 ──────────────────────────────────────────
  const issues = [];

  if (fp.webdriver === true)
    issues.push({ level: "high", msg: "navigator.webdriver = true，自动化特征已暴露" });
  if (fp.plugins_count === 0)
    issues.push({ level: "medium", msg: "navigator.plugins 为空（自动化/无扩展环境）" });
  if (fp.is_headless_ua)
    issues.push({ level: "high", msg: `User-Agent 含自动化标志: ${fp.user_agent}` });
  if (fp.outer_width === 0 || fp.outer_height === 0)
    issues.push({ level: "medium", msg: `outerWidth/outerHeight = ${fp.outer_width}x${fp.outer_height}，疑似无头模式` });
  if (fp.webgl_is_swiftshader)
    issues.push({ level: "high", msg: `WebGL 软件渲染: ${fp.webgl_renderer}（无头特征）` });
  if (fp.notifications_permission === "denied")
    issues.push({ level: "low", msg: "通知权限被拒绝（无头浏览器常见特征）" });
  if (fp.visibility_state !== "visible")
    issues.push({ level: "medium", msg: `document.visibilityState = "${fp.visibility_state}"（visibilityState 伪装失效）` });
  if (ps.has_captcha_modal)
    issues.push({ level: "high", msg: "检测到页面验证码/人机验证弹窗" });
  if (ps.page_is_404)
    issues.push({ level: "high", msg: "当前页面为 404 错误页（风控拦截跳转）" });
  if (ps.page_has_risk_block)
    issues.push({ level: "high", msg: "检测到风控封锁页面元素" });
  if (ps.api_failures_on_page.length > 0)
    issues.push({ level: "medium", msg: `页面已有 ${ps.api_failures_on_page.length} 个 API 请求被风控拦截` });
  if (ps.state_risk_keys && ps.state_risk_keys.length > 0)
    issues.push({ level: "high", msg: `__INITIAL_STATE__ 含风控字段: ${ps.state_risk_keys.join(", ")}` });

  for (const [key, probe] of Object.entries(report.api_probes)) {
    if (probe.risk_blocked)
      issues.push({ level: "high", msg: `API ${key} 返回风控状态码 ${probe.status}` });
    else if (probe.xhs_code === 300012 || probe.xhs_code === -9001)
      issues.push({ level: "high", msg: `API ${key} 返回封号/风控码 ${probe.xhs_code}: ${probe.xhs_msg}` });
    else if (probe.xhs_code === -1)
      issues.push({ level: "medium", msg: `API ${key} 返回系统繁忙 (-1): ${probe.xhs_msg}` });
  }

  report.issues = issues;
  const high = issues.filter(i => i.level === "high").length;
  const med  = issues.filter(i => i.level === "medium").length;
  if (high >= 2)       report.risk_level = "high";
  else if (high >= 1 || med >= 2) report.risk_level = "medium";
  else if (med >= 1 || issues.length > 0) report.risk_level = "low";
  else                 report.risk_level = "safe";

  return report;
}

// ───────────────────────── Tab 管理 ─────────────────────────

async function getOrOpenXhsTab() {
  const tabs = await chrome.tabs.query({
    url: [
      "https://www.xiaohongshu.com/*",
      "https://xiaohongshu.com/*",
      "https://creator.xiaohongshu.com/*",
    ],
  });
  if (tabs.length > 0) return tabs[0];
  // 没有已打开的 XHS 页面，新建一个
  const tab = await chrome.tabs.create({ url: "https://www.xiaohongshu.com/" });
  await waitForTabComplete(tab.id, null, 30000);
  return tab;
}

// ───────────────────────── 启动 ─────────────────────────

connect();
