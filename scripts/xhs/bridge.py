"""BridgePage - 通过浏览器扩展 Bridge 实现与 CDP Page 相同的接口。

CLI 命令通过 WebSocket 发送到 bridge_server.py，
bridge_server 转发给浏览器扩展执行，结果原路返回。

每次调用都是一次短连接（发一条命令 → 收一条回复），
不需要维护持久连接。
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import websockets.sync.client as ws_client

from .errors import CDPError, ElementNotFoundError

BRIDGE_URL = "ws://localhost:9333"


class BridgePage:
    """与 CDP Page 接口兼容的 Extension Bridge 实现。"""

    def __init__(self, bridge_url: str = BRIDGE_URL) -> None:
        self._bridge_url = bridge_url

    # ─── 内部通信 ───────────────────────────────────────────────

    def _call(self, method: str, params: dict | None = None) -> Any:
        """向 bridge server 发送一条命令并等待结果。"""
        msg: dict[str, Any] = {"role": "cli", "method": method}
        if params:
            msg["params"] = params
        try:
            with ws_client.connect(self._bridge_url, max_size=50 * 1024 * 1024) as ws:
                ws.send(json.dumps(msg, ensure_ascii=False))
                raw = ws.recv(timeout=90)
        except OSError as e:
            raise CDPError(f"无法连接到 bridge server（ws://localhost:9333）: {e}") from e

        resp = json.loads(raw)
        if "error" in resp and resp["error"]:
            raise CDPError(f"Bridge 错误: {resp['error']}")
        return resp.get("result")

    # ─── 导航 ───────────────────────────────────────────────────

    def navigate(self, url: str) -> None:
        self._call("navigate", {"url": url})

    def wait_for_load(self, timeout: float = 60.0) -> None:
        self._call("wait_for_load", {"timeout": int(timeout * 1000)})

    def wait_dom_stable(self, timeout: float = 10.0, interval: float = 0.5) -> None:
        self._call(
            "wait_dom_stable",
            {
                "timeout": int(timeout * 1000),
                "interval": int(interval * 1000),
            },
        )

    # ─── JavaScript 执行 ────────────────────────────────────────

    def evaluate(self, expression: str, timeout: float = 30.0) -> Any:
        return self._call("evaluate", {"expression": expression})

    def evaluate_function(self, function_body: str, *args: Any) -> Any:
        return self._call("evaluate", {"expression": f"({function_body})()"})

    # ─── 元素查询 ────────────────────────────────────────────────

    def query_selector(self, selector: str) -> str | None:
        """返回 "found" 表示元素存在，None 表示不存在（兼容 CDP 的 objectId 语义）。"""
        found = self._call("has_element", {"selector": selector})
        return "found" if found else None

    def query_selector_all(self, selector: str) -> list[str]:
        count = self.get_elements_count(selector)
        return ["found"] * count

    def has_element(self, selector: str) -> bool:
        return bool(self._call("has_element", {"selector": selector}))

    def wait_for_element(self, selector: str, timeout: float = 30.0) -> str:
        found = self._call(
            "wait_for_selector",
            {
                "selector": selector,
                "timeout": int(timeout * 1000),
            },
        )
        if not found:
            raise ElementNotFoundError(selector)
        return "found"

    # ─── 元素操作 ────────────────────────────────────────────────

    def click_element(self, selector: str) -> None:
        self.evaluate(
            f"""
            (() => {{
                const nodes = Array.from(document.querySelectorAll({json.dumps(selector)}));
                if (!nodes.length) throw new Error('元素不存在: ' + {json.dumps(selector)});
                const visible = nodes.find((el) => {{
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden';
                }}) || nodes[0];
                const target = visible.closest("button,[role='button'],a,[tabindex]") || visible;
                target.scrollIntoView({{block: 'center'}});
                if (typeof target.focus === 'function') target.focus();

                if (typeof target.click === 'function') {{
                    target.click();
                    return;
                }}

                const rect = target.getBoundingClientRect();
                const x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;
                const pointEl = document.elementFromPoint(x, y);
                const dispatchTarget =
                    pointEl && (pointEl === target || target.contains(pointEl))
                        ? pointEl
                        : target;

                ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach((t) => {{
                    dispatchTarget.dispatchEvent(new MouseEvent(t, {{
                        bubbles: true,
                        cancelable: true,
                        clientX: x,
                        clientY: y,
                    }}));
                }});
            }})()
            """
        )

    def click_element_by_text(self, selector: str, text: str) -> None:
        self._call("click_element_by_text", {"selector": selector, "text": text})

    def input_text(self, selector: str, text: str) -> None:
        self._call("input_text", {"selector": selector, "text": text})

    def input_content_editable(self, selector: str, text: str) -> None:
        self._call("input_content_editable", {"selector": selector, "text": text})

    def get_element_text(self, selector: str) -> str | None:
        return self._call("get_element_text", {"selector": selector})

    def get_element_attribute(self, selector: str, attr: str) -> str | None:
        return self._call("get_element_attribute", {"selector": selector, "attr": attr})

    def get_elements_count(self, selector: str) -> int:
        result = self._call("get_elements_count", {"selector": selector})
        return int(result) if result is not None else 0

    def remove_element(self, selector: str) -> None:
        self._call("remove_element", {"selector": selector})

    def hover_element(self, selector: str) -> None:
        self._call("hover_element", {"selector": selector})

    def select_all_text(self, selector: str) -> None:
        self._call("select_all_text", {"selector": selector})

    # ─── 滚动 ────────────────────────────────────────────────────

    def scroll_by(self, x: int, y: int) -> None:
        self._call("scroll_by", {"x": x, "y": y})

    def scroll_to(self, x: int, y: int) -> None:
        self._call("scroll_to", {"x": x, "y": y})

    def scroll_to_bottom(self) -> None:
        self._call("scroll_to_bottom")

    def scroll_element_into_view(self, selector: str) -> None:
        self._call("scroll_element_into_view", {"selector": selector})

    def scroll_nth_element_into_view(self, selector: str, index: int) -> None:
        self._call("scroll_nth_element_into_view", {"selector": selector, "index": index})

    def get_scroll_top(self) -> int:
        result = self._call("get_scroll_top")
        return int(result) if result is not None else 0

    def get_viewport_height(self) -> int:
        result = self._call("get_viewport_height")
        return int(result) if result is not None else 768

    # ─── 输入事件 ────────────────────────────────────────────────

    def press_key(self, key: str) -> None:
        self._call("press_key", {"key": key})

    def type_text(self, text: str, delay_ms: int = 50) -> None:
        self._call("type_text", {"text": text, "delayMs": delay_ms})

    def mouse_move(self, x: float, y: float) -> None:
        self._call("mouse_move", {"x": x, "y": y})

    def mouse_click(self, x: float, y: float, button: str = "left") -> None:
        self._call("mouse_click", {"x": x, "y": y, "button": button})

    def dispatch_wheel_event(self, delta_y: float) -> None:
        self._call("dispatch_wheel_event", {"deltaY": delta_y})

    # ─── 文件上传 ────────────────────────────────────────────────

    def set_file_input(self, selector: str, files: list[str]) -> None:
        """通过 chrome.debugger + DOM.setFileInputFiles 上传本地文件。
        传递绝对路径给扩展，由扩展调用 CDP 完成上传（与原 CDP 方式等价）。
        """
        # 统一转换为绝对路径（兼容 Windows 反斜杠）
        abs_paths = [os.path.abspath(path) for path in files]
        self._call("set_file_input", {"selector": selector, "files": abs_paths})

    # ─── 截图 ────────────────────────────────────────────────────

    def screenshot_element(self, selector: str, padding: int = 0) -> bytes:
        result = self._call("screenshot_element", {"selector": selector, "padding": padding})
        if result and result.get("data"):
            return base64.b64decode(result["data"])
        return b""

    # ─── 风控分析 ────────────────────────────────────────────────

    def get_404_diagnostics(self) -> list[dict]:
        """获取拦截器捕获的 404/461/403/999 诊断事件列表（最近 50 条）。
        每条事件包含请求上下文、cookie 快照、以及根因分析。
        """
        return self._call("get_404_diagnostics") or []

    def clear_404_diagnostics(self) -> None:
        """清空已存储的诊断事件。"""
        self._call("clear_404_diagnostics")

    def analyze_risk_control(self, probe_urls: list[str] | None = None) -> dict:
        """分析小红书风控状态。
        检查自动化指纹（webdriver、plugins、WebGL 等）、页面风控元素、
        并实时探测关键 API 端点的响应状态。
        返回结构化报告，含 risk_level（safe/low/medium/high）和 issues 列表。
        """
        return self._call("analyze_risk_control", {"probeUrls": probe_urls or []})

    # ─── NetLog 风控数据 ─────────────────────────────────────────

    def get_netlog_enabled(self) -> bool:
        """检查 NetLog 是否已启用（用户在扩展 popup 上彩蛋激活）。"""
        result = self._call("get_netlog_enabled") or {}
        return bool(result.get("enabled"))

    def get_netlog(self) -> list[dict]:
        """获取当前会话的全部 NetLog entries（最多 500 条环形缓冲）。"""
        result = self._call("get_netlog") or {}
        return result.get("entries") or []

    # ─── 无操作（原 CDP 专有功能，扩展模式不需要） ─────────────────

    def inject_stealth(self) -> None:
        """不需要注入 stealth 脚本——直接使用用户浏览器，无需伪装。"""

    # ─── 兼容性辅助方法 ──────────────────────────────────────────

    def is_server_running(self) -> bool:
        """检查 bridge server 是否在运行（不需要 extension 已连接）。"""
        try:
            with ws_client.connect(self._bridge_url, open_timeout=3) as ws:
                ws.send(json.dumps({"role": "cli", "method": "ping_server"}))
                raw = ws.recv(timeout=5)
            resp = json.loads(raw)
            return "result" in resp
        except Exception:
            return False

    def is_extension_connected(self) -> bool:
        """检查浏览器扩展是否已连接到 bridge server。"""
        try:
            with ws_client.connect(self._bridge_url, open_timeout=3) as ws:
                ws.send(json.dumps({"role": "cli", "method": "ping_server"}))
                raw = ws.recv(timeout=5)
            resp = json.loads(raw)
            return bool(resp.get("result", {}).get("extension_connected"))
        except Exception:
            return False

    @property
    def target_id(self) -> str:
        """兼容旧代码对 page.target_id 的引用。"""
        return "extension-bridge"
