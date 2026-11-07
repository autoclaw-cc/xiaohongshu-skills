/**
 * XHS Bridge - Content Script（隔离 world）
 *
 * 接收来自 background.js 的 DOM 操作命令并执行。
 * evaluate / has_element 等需要访问页面 JS 变量的命令由 background.js
 * 通过 chrome.scripting.executeScript(world:"MAIN") 直接处理，不经过这里。
 *
 * 同时负责接收来自 interceptor.js（MAIN world）的 postMessage，
 * 将 404 诊断事件转发给 background.js 存储。
 */

// ── interceptor.js (MAIN world) → background.js 桥接 ────────
// 注意：扩展 reload 后老 content script 仍在页面，但 chrome.runtime context 失效。
// 此时 sendMessage 会同步抛 "Extension context invalidated"（.catch 抓不到），
// 必须先用 chrome.runtime?.id 检测 context 是否还存活。
window.addEventListener("message", (e) => {
  if (e.source !== window) return;
  if (e.data?.source !== "xhs-interceptor" || e.data?.type !== "BLOCK_EVENT") return;
  if (!chrome.runtime?.id) return; // context invalidated, orphan content script
  try {
    chrome.runtime.sendMessage({ type: "XHS_BLOCK_EVENT", event: e.data.event }).catch(() => {});
  } catch (_) { /* runtime gone between guard and call */ }
});

// 通知 interceptor.js：content.js 已就绪，可以 flush 排队的事件
window.postMessage({ source: "xhs-content-ready" }, "*");

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  handleDomCommand(msg.method, msg.params || {})
    .then((result) => sendResponse({ result: result ?? null }))
    .catch((err) => sendResponse({ error: String(err.message || err) }));
  return true; // 异步响应
});

async function handleDomCommand(method, params) {
  switch (method) {
    case "click_element":
      return cmdClickElement(params);

    case "input_text":
      return cmdInputText(params);

    case "input_content_editable":
      return cmdInputContentEditable(params);

    case "scroll_by":
      window.scrollBy(params.x || 0, params.y || 0);
      return null;

    case "scroll_to":
      window.scrollTo(params.x || 0, params.y || 0);
      return null;

    case "scroll_to_bottom":
      window.scrollTo(0, document.body.scrollHeight);
      return null;

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
      const target =
        document.querySelector(".note-scroller") ||
        document.querySelector(".interaction-container") ||
        document.documentElement;
      target.dispatchEvent(
        new WheelEvent("wheel", {
          deltaY: params.deltaY || 0,
          deltaMode: 0,
          bubbles: true,
          cancelable: true,
          view: window,
        }),
      );
      return null;
    }

    case "mouse_move": {
      document.dispatchEvent(
        new MouseEvent("mousemove", { clientX: params.x, clientY: params.y, bubbles: true }),
      );
      return null;
    }

    case "mouse_click": {
      const el = document.elementFromPoint(params.x, params.y);
      if (el) {
        el.dispatchEvent(new MouseEvent("mousedown", { clientX: params.x, clientY: params.y, bubbles: true }));
        el.dispatchEvent(new MouseEvent("mouseup", { clientX: params.x, clientY: params.y, bubbles: true }));
        el.dispatchEvent(new MouseEvent("click", { clientX: params.x, clientY: params.y, bubbles: true }));
      }
      return null;
    }

    case "press_key": {
      const keyMap = {
        Enter: { key: "Enter", code: "Enter", keyCode: 13 },
        ArrowDown: { key: "ArrowDown", code: "ArrowDown", keyCode: 40 },
        Tab: { key: "Tab", code: "Tab", keyCode: 9 },
        Backspace: { key: "Backspace", code: "Backspace", keyCode: 8 },
      };
      const info = keyMap[params.key] || { key: params.key, code: params.key, keyCode: 0 };
      const active = document.activeElement || document.body;
      active.dispatchEvent(new KeyboardEvent("keydown", { ...info, bubbles: true }));
      active.dispatchEvent(new KeyboardEvent("keyup", { ...info, bubbles: true }));
      return null;
    }

    case "type_text": {
      const active = document.activeElement || document.body;
      const delay = params.delayMs || 50;
      for (const char of params.text) {
        active.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
        active.dispatchEvent(new KeyboardEvent("keypress", { key: char, bubbles: true }));
        active.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
        await sleep(delay);
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
        const x = rect.left + rect.width / 2;
        const y = rect.top + rect.height / 2;
        el.dispatchEvent(new MouseEvent("mouseover", { clientX: x, clientY: y, bubbles: true }));
        el.dispatchEvent(new MouseEvent("mousemove", { clientX: x, clientY: y, bubbles: true }));
      }
      return null;
    }

    case "select_all_text": {
      const el = document.querySelector(params.selector);
      if (el) {
        el.focus();
        if (el.select) el.select();
        else document.execCommand("selectAll");
      }
      return null;
    }

    case "set_file_input":
      return cmdSetFileInput(params);

    default:
      throw new Error(`content.js: 未知命令 ${method}`);
  }
}

// ───────────────────────── 具体实现 ─────────────────────────

function cmdClickElement({ selector }) {
  const el = document.querySelector(selector);
  if (!el) throw new Error(`元素不存在: ${selector}`);
  el.scrollIntoView({ block: "center" });
  el.click();
  return null;
}

function cmdInputText({ selector, text }) {
  const el = document.querySelector(selector);
  if (!el) throw new Error(`元素不存在: ${selector}`);
  el.focus();
  el.value = text;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  return null;
}

async function cmdInputContentEditable({ selector, text }) {
  const el = document.querySelector(selector);
  if (!el) throw new Error(`元素不存在: ${selector}`);
  el.focus();
  // 全选清空
  document.execCommand("selectAll", false, null);
  document.execCommand("delete", false, null);
  await sleep(80);
  // 逐行插入（换行转为 Enter 键事件）
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    if (lines[i]) document.execCommand("insertText", false, lines[i]);
    if (i < lines.length - 1) {
      el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true }));
      await sleep(40);
    }
  }
  return null;
}

async function cmdSetFileInput({ selector, files }) {
  const el = document.querySelector(selector);
  if (!el) throw new Error(`文件输入框不存在: ${selector}`);

  const dt = new DataTransfer();
  for (const f of files) {
    const bytes = Uint8Array.from(atob(f.data), (c) => c.charCodeAt(0));
    const blob = new Blob([bytes], { type: f.type });
    dt.items.add(new File([blob], f.name, { type: f.type }));
  }

  Object.defineProperty(el, "files", {
    value: dt.files,
    configurable: true,
    writable: true,
  });

  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("input", { bubbles: true }));
  return null;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// ─── NetLog 信号转发 + 启用状态同步 ──────────────────────────────

// MAIN world interceptor → content (postMessage) → background (runtime)
window.addEventListener("message", (e) => {
  if (e.source !== window) return;
  if (e.data?.source !== "xhs-netlog-intercept") return;
  if (!chrome.runtime?.id) return; // extension reloaded, content script orphaned
  try {
    chrome.runtime.sendMessage({
      type: "NETLOG_INTERCEPTOR_ENTRY",
      payload: e.data,
    }).catch(() => {});
  } catch (_) { /* runtime gone between guard and call */ }
});

