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

const BRIDGE_URL = "ws://localhost:9333";
let ws = null;

// 保持 service worker 存活：有开放的 WebSocket 连接时 Chrome 不会终止 SW
// 额外加 alarm 作为保底
chrome.alarms.create("keepAlive", { periodInMinutes: 0.4 });
chrome.alarms.onAlarm.addListener(() => {
  if (!ws || ws.readyState !== WebSocket.OPEN) connect();
});

// ───────────────────────── WebSocket ─────────────────────────

function connect() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;

  ws = new WebSocket(BRIDGE_URL);

  ws.onopen = () => {
    console.log("[XHS Bridge] 已连接到 bridge server");
    ws.send(JSON.stringify({ role: "extension" }));
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
      return await cmdEvaluateInMainWorld(method, params);

    // ── DOM 操作（在页面 MAIN world 执行，无需 content script 就绪） ──
    default:
      return await cmdDomInMainWorld(method, params);
  }
}

// ───────────────────────── 导航 ─────────────────────────

async function cmdNavigate({ url }) {
  const tab = await getOrOpenXhsTab();
  await chrome.tabs.update(tab.id, { url });
  await waitForTabComplete(tab.id, url, 60000);
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
      return el ? el.textContent : null;
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

    default:
      return { __xhs_error: `未知 MAIN world 方法: ${method}` };
  }
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
    case "click_element": {
      const el = requireEl(params.selector);
      if (el.__xhs_error) return el;
      el.scrollIntoView({ block: "center" });
      el.focus();
      el.click();
      return null;
    }

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

    case "press_key": {
      const active = document.activeElement || document.body;
      const inCE = active.isContentEditable;
      if (inCE && params.key === "Enter") {
        document.execCommand("insertParagraph", false, null);
        return null;
      }
      if (inCE && params.key === "ArrowDown") {
        // 将光标移到内容末尾（等价于多次下移到底）
        const sel = window.getSelection();
        if (sel && active.childNodes.length) {
          sel.selectAllChildren(active);
          sel.collapseToEnd();
        }
        return null;
      }
      const keyMap = {
        Enter: { key: "Enter", code: "Enter", keyCode: 13 },
        ArrowDown: { key: "ArrowDown", code: "ArrowDown", keyCode: 40 },
        Tab: { key: "Tab", code: "Tab", keyCode: 9 },
        Backspace: { key: "Backspace", code: "Backspace", keyCode: 8 },
      };
      const info = keyMap[params.key] || { key: params.key, code: params.key, keyCode: 0 };
      active.dispatchEvent(new KeyboardEvent("keydown", { ...info, bubbles: true }));
      active.dispatchEvent(new KeyboardEvent("keyup", { ...info, bubbles: true }));
      return null;
    }

    case "type_text": {
      return new Promise(async (resolve) => {
        const active = document.activeElement || document.body;
        const inCE = active.isContentEditable;
        for (const char of params.text) {
          if (inCE) {
            document.execCommand("insertText", false, char);
          } else {
            active.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
            active.dispatchEvent(new KeyboardEvent("keypress", { key: char, bubbles: true }));
            active.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
          }
          await sleep(params.delayMs || 50);
        }
        resolve(null);
      });
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

// ───────────────────────── Tab 管理 ─────────────────────────

async function getOrOpenXhsTab() {
  const xhsUrls = [
    "https://www.xiaohongshu.com/*",
    "https://xiaohongshu.com/*",
    "https://creator.xiaohongshu.com/*",
    "https://www.rednote.com/*",
    "https://rednote.com/*",
    "https://creator.rednote.com/*",
  ];
  const tabs = await chrome.tabs.query({ url: xhsUrls });
  if (tabs.length > 0) return tabs[0];
  // 没有已打开的 XHS/RedNote 页面，新建一个
  const tab = await chrome.tabs.create({ url: "https://www.xiaohongshu.com/" });
  await waitForTabComplete(tab.id, null, 30000);
  return tab;
}

// ───────────────────────── 启动 ─────────────────────────

connect();
