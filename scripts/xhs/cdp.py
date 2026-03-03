"""CDP WebSocket 客户端（Browser, Page, Element），对应 Go browser/browser.go + go-rod API。

通过原生 WebSocket 与 Chrome DevTools Protocol 通信，实现浏览器自动化控制。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests
import websockets.sync.client as ws_client

from .errors import CDPError, ElementNotFoundError
from .stealth import STEALTH_JS

logger = logging.getLogger(__name__)


class CDPClient:
    """底层 CDP WebSocket 通信客户端。"""

    def __init__(self, ws_url: str) -> None:
        self._ws = ws_client.connect(ws_url, max_size=50 * 1024 * 1024)
        self._id = 0
        self._callbacks: dict[int, Any] = {}

    def send(self, method: str, params: dict | None = None) -> dict:
        """发送 CDP 命令并等待结果。"""
        self._id += 1
        msg: dict[str, Any] = {"id": self._id, "method": method}
        if params:
            msg["params"] = params
        self._ws.send(json.dumps(msg))
        return self._wait_for(self._id)

    def _wait_for(self, msg_id: int, timeout: float = 30.0) -> dict:
        """等待指定 id 的响应。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = self._ws.recv(timeout=max(0.1, deadline - time.monotonic()))
            except TimeoutError:
                break
            data = json.loads(raw)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise CDPError(f"CDP 错误: {data['error']}")
                return data.get("result", {})
        raise CDPError(f"等待 CDP 响应超时 (id={msg_id})")

    def close(self) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            self._ws.close()


class Page:
    """CDP 页面对象，封装常用操作。"""

    def __init__(self, cdp: CDPClient, target_id: str, session_id: str) -> None:
        self._cdp = cdp
        self.target_id = target_id
        self.session_id = session_id
        self._ws = cdp._ws
        self._id_counter = 1000

    def _send_session(self, method: str, params: dict | None = None) -> dict:
        """向 session 发送命令。"""
        self._id_counter += 1
        msg: dict[str, Any] = {
            "id": self._id_counter,
            "method": method,
            "sessionId": self.session_id,
        }
        if params:
            msg["params"] = params
        self._ws.send(json.dumps(msg))
        return self._wait_session(self._id_counter)

    def _wait_session(self, msg_id: int, timeout: float = 60.0) -> dict:
        """等待 session 响应。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = self._ws.recv(timeout=max(0.1, deadline - time.monotonic()))
            except TimeoutError:
                break
            data = json.loads(raw)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise CDPError(f"CDP 错误: {data['error']}")
                return data.get("result", {})
        raise CDPError(f"等待 session 响应超时 (id={msg_id})")

    def navigate(self, url: str) -> None:
        """导航到指定 URL。"""
        logger.info("导航到: %s", url)
        self._send_session("Page.navigate", {"url": url})

    def wait_for_load(self, timeout: float = 60.0) -> None:
        """等待页面加载完成（通过轮询 document.readyState）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                state = self.evaluate("document.readyState")
                if state == "complete":
                    return
            except CDPError:
                pass
            time.sleep(0.5)
        logger.warning("等待页面加载超时")

    def wait_dom_stable(self, timeout: float = 10.0, interval: float = 0.5) -> None:
        """等待 DOM 稳定（连续两次 DOM 快照一致）。"""
        last_html = ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                html = self.evaluate("document.body ? document.body.innerHTML.length : 0")
                if html == last_html and html != "":
                    return
                last_html = html
            except CDPError:
                pass
            time.sleep(interval)

    def evaluate(self, expression: str, timeout: float = 30.0) -> Any:
        """执行 JavaScript 表达式并返回结果。"""
        result = self._send_session(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": False,
            },
        )
        if "exceptionDetails" in result:
            raise CDPError(f"JS 执行异常: {result['exceptionDetails']}")
        remote_obj = result.get("result", {})
        return remote_obj.get("value")

    def evaluate_function(self, function_body: str, *args: Any) -> Any:
        """执行 JavaScript 函数并返回结果。

        function_body 是一个完整的函数体，如 `() => { return 1; }`
        """
        result = self._send_session(
            "Runtime.evaluate",
            {
                "expression": f"({function_body})()",
                "returnByValue": True,
                "awaitPromise": False,
            },
        )
        if "exceptionDetails" in result:
            raise CDPError(f"JS 函数执行异常: {result['exceptionDetails']}")
        remote_obj = result.get("result", {})
        return remote_obj.get("value")

    def query_selector(self, selector: str) -> str | None:
        """查找单个元素，返回 objectId 或 None。"""
        result = self._send_session(
            "Runtime.evaluate",
            {
                "expression": f"document.querySelector({json.dumps(selector)})",
                "returnByValue": False,
            },
        )
        remote_obj = result.get("result", {})
        if remote_obj.get("subtype") == "null" or remote_obj.get("type") == "undefined":
            return None
        return remote_obj.get("objectId")

    def query_selector_all(self, selector: str) -> list[str]:
        """查找多个元素，返回 objectId 列表。"""
        # 通过 JS 返回元素数量，然后逐个获取
        count = self.evaluate(f"document.querySelectorAll({json.dumps(selector)}).length")
        if not count:
            return []
        object_ids = []
        for i in range(count):
            result = self._send_session(
                "Runtime.evaluate",
                {
                    "expression": (f"document.querySelectorAll({json.dumps(selector)})[{i}]"),
                    "returnByValue": False,
                },
            )
            obj = result.get("result", {})
            oid = obj.get("objectId")
            if oid:
                object_ids.append(oid)
        return object_ids

    def has_element(self, selector: str) -> bool:
        """检查元素是否存在。"""
        return self.evaluate(f"document.querySelector({json.dumps(selector)}) !== null") is True

    def wait_for_element(self, selector: str, timeout: float = 30.0) -> str:
        """等待元素出现，返回 objectId。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            oid = self.query_selector(selector)
            if oid:
                return oid
            time.sleep(0.5)
        raise ElementNotFoundError(selector)

    def click_element(self, selector: str) -> None:
        """点击指定选择器的元素。"""
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (el) el.click();
            }})()
            """
        )

    def input_text(self, selector: str, text: str) -> None:
        """向指定选择器的元素输入文本。"""
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return;
                el.focus();
                el.value = {json.dumps(text)};
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
            }})()
            """
        )

    def input_content_editable(self, selector: str, text: str) -> None:
        """向 contentEditable 元素输入文本（如 div.ql-editor）。"""
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return;
                el.focus();
                el.textContent = {json.dumps(text)};
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
            }})()
            """
        )

    def get_element_text(self, selector: str) -> str | None:
        """获取元素文本内容。"""
        return self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                return el ? el.textContent : null;
            }})()
            """
        )

    def get_element_attribute(self, selector: str, attr: str) -> str | None:
        """获取元素属性值。"""
        return self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                return el ? el.getAttribute({json.dumps(attr)}) : null;
            }})()
            """
        )

    def get_elements_count(self, selector: str) -> int:
        """获取匹配元素数量。"""
        result = self.evaluate(f"document.querySelectorAll({json.dumps(selector)}).length")
        return result if isinstance(result, int) else 0

    def scroll_by(self, x: int, y: int) -> None:
        """滚动页面。"""
        self.evaluate(f"window.scrollBy({x}, {y})")

    def scroll_to(self, x: int, y: int) -> None:
        """滚动到指定位置。"""
        self.evaluate(f"window.scrollTo({x}, {y})")

    def scroll_to_bottom(self) -> None:
        """滚动到页面底部。"""
        self.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    def scroll_element_into_view(self, selector: str) -> None:
        """将元素滚动到可视区域。"""
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (el) el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
            }})()
            """
        )

    def scroll_nth_element_into_view(self, selector: str, index: int) -> None:
        """将第 N 个匹配元素滚动到可视区域。"""
        self.evaluate(
            f"""
            (() => {{
                const els = document.querySelectorAll({json.dumps(selector)});
                if (els[{index}]) els[{index}].scrollIntoView(
                    {{behavior: 'smooth', block: 'center'}}
                );
            }})()
            """
        )

    def get_scroll_top(self) -> int:
        """获取当前滚动位置。"""
        result = self.evaluate(
            "window.pageYOffset || document.documentElement.scrollTop"
            " || document.body.scrollTop || 0"
        )
        return int(result) if result else 0

    def get_viewport_height(self) -> int:
        """获取视口高度。"""
        result = self.evaluate("window.innerHeight")
        return int(result) if result else 768

    def set_file_input(self, selector: str, files: list[str]) -> None:
        """设置文件输入框的文件（通过 CDP DOM.setFileInputFiles）。"""
        # 先获取 nodeId
        doc = self._send_session("DOM.getDocument", {"depth": 0})
        root_node_id = doc["root"]["nodeId"]
        result = self._send_session(
            "DOM.querySelector",
            {"nodeId": root_node_id, "selector": selector},
        )
        node_id = result.get("nodeId", 0)
        if node_id == 0:
            raise ElementNotFoundError(selector)
        self._send_session(
            "DOM.setFileInputFiles",
            {"nodeId": node_id, "files": files},
        )

    def dispatch_wheel_event(self, delta_y: float) -> None:
        """触发滚轮事件以激活懒加载。"""
        self.evaluate(
            f"""
            (() => {{
                let target = document.querySelector('.note-scroller')
                    || document.querySelector('.interaction-container')
                    || document.documentElement;
                const event = new WheelEvent('wheel', {{
                    deltaY: {delta_y},
                    deltaMode: 0,
                    bubbles: true,
                    cancelable: true,
                    view: window,
                }});
                target.dispatchEvent(event);
            }})()
            """
        )

    def mouse_move(self, x: float, y: float) -> None:
        """移动鼠标。"""
        self._send_session(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": x, "y": y},
        )

    def mouse_click(self, x: float, y: float, button: str = "left") -> None:
        """在指定坐标点击。"""
        self._send_session(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": x, "y": y, "button": button, "clickCount": 1},
        )
        self._send_session(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", "x": x, "y": y, "button": button, "clickCount": 1},
        )

    def type_text(self, text: str, delay_ms: int = 50) -> None:
        """逐字符输入文本。"""
        for char in text:
            self._send_session(
                "Input.dispatchKeyEvent",
                {"type": "keyDown", "text": char},
            )
            self._send_session(
                "Input.dispatchKeyEvent",
                {"type": "keyUp", "text": char},
            )
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)

    def press_key(self, key: str) -> None:
        """按下并释放指定键。"""
        key_map = {
            "Enter": {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
            "ArrowDown": {
                "key": "ArrowDown",
                "code": "ArrowDown",
                "windowsVirtualKeyCode": 40,
            },
            "Tab": {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9},
        }
        info = key_map.get(key, {"key": key, "code": key})
        self._send_session(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", **info},
        )
        self._send_session(
            "Input.dispatchKeyEvent",
            {"type": "keyUp", **info},
        )

    def inject_stealth(self) -> None:
        """注入反检测脚本。"""
        self._send_session(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": STEALTH_JS},
        )

    def remove_element(self, selector: str) -> None:
        """移除 DOM 元素。"""
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (el) el.remove();
            }})()
            """
        )

    def hover_element(self, selector: str) -> None:
        """悬停到元素中心。"""
        box = self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return null;
                const rect = el.getBoundingClientRect();
                return {{x: rect.left + rect.width / 2, y: rect.top + rect.height / 2}};
            }})()
            """
        )
        if box:
            self.mouse_move(box["x"], box["y"])

    def select_all_text(self, selector: str) -> None:
        """选中输入框内所有文本。"""
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return;
                el.focus();
                el.select ? el.select() : document.execCommand('selectAll');
            }})()
            """
        )


class Browser:
    """Chrome 浏览器 CDP 控制器。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 9222) -> None:
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self._cdp: CDPClient | None = None

    def connect(self) -> None:
        """连接到 Chrome DevTools。"""
        resp = requests.get(f"{self.base_url}/json/version", timeout=5)
        resp.raise_for_status()
        info = resp.json()
        ws_url = info["webSocketDebuggerUrl"]
        logger.info("连接到 Chrome: %s", ws_url)
        self._cdp = CDPClient(ws_url)

    def new_page(self, url: str = "about:blank") -> Page:
        """创建新页面。"""
        if not self._cdp:
            self.connect()
        assert self._cdp is not None

        # 创建 target
        result = self._cdp.send("Target.createTarget", {"url": url})
        target_id = result["targetId"]

        # 附加到 target
        result = self._cdp.send(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        session_id = result["sessionId"]

        page = Page(self._cdp, target_id, session_id)

        # 启用必要的 domain
        page._send_session("Page.enable")
        page._send_session("DOM.enable")
        page._send_session("Runtime.enable")

        # 注入反检测
        page.inject_stealth()

        return page

    def get_existing_page(self) -> Page | None:
        """获取已有页面（取第一个非 about:blank 的 page target）。"""
        if not self._cdp:
            self.connect()
        assert self._cdp is not None

        resp = requests.get(f"{self.base_url}/json", timeout=5)
        targets = resp.json()

        for target in targets:
            if target.get("type") == "page" and target.get("url") != "about:blank":
                target_id = target["id"]
                result = self._cdp.send(
                    "Target.attachToTarget",
                    {"targetId": target_id, "flatten": True},
                )
                session_id = result["sessionId"]
                page = Page(self._cdp, target_id, session_id)
                page._send_session("Page.enable")
                page._send_session("DOM.enable")
                page._send_session("Runtime.enable")
                page.inject_stealth()
                return page
        return None

    def close_page(self, page: Page) -> None:
        """关闭页面。"""
        import contextlib

        if self._cdp:
            with contextlib.suppress(CDPError):
                self._cdp.send("Target.closeTarget", {"targetId": page.target_id})

    def close(self) -> None:
        """关闭连接。"""
        if self._cdp:
            self._cdp.close()
            self._cdp = None
