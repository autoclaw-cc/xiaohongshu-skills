"""网页文字私信：核对收件人、填写预览、单次发送并等待服务端消息标识。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .bridge import BridgePage
from .errors import NotLoggedInError, XHSError

CHAT_URL = "https://www.xiaohongshu.com/chat"
MAX_TEXT_LENGTH = 1000  # 网页按 JavaScript UTF-16 length 限制输入长度。
_SCRIPT = Path(__file__).with_name("direct_message.js").read_text(encoding="utf-8")


class DirectMessageError(XHSError):
    """私信发送前校验失败。"""


def read_message_file(path: str) -> str:
    """在连接浏览器前校验文件和文字，防止网页静默截断。"""
    file = Path(path)
    if not file.is_absolute():
        raise DirectMessageError("私信正文文件必须使用绝对路径")
    try:
        content = file.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    except (OSError, UnicodeError) as exc:
        raise DirectMessageError("无法读取 UTF-8 私信正文文件") from exc
    validate_content(content)
    return content


def validate_content(content: str) -> None:
    if not content.strip():
        raise DirectMessageError("私信正文不可为空")
    if len(content.encode("utf-16-le")) // 2 > MAX_TEXT_LENGTH:
        raise DirectMessageError("私信正文超过网页 1000 字符限制（表情可能占两个字符）")


def _run(page: BridgePage, action: str, **params: Any) -> dict:
    result = page.evaluate(f"({_SCRIPT})({json.dumps({'action': action, **params})})")
    if not isinstance(result, dict):
        raise DirectMessageError("无法读取私信页面状态")
    if result.get("not_logged_in"):
        raise NotLoggedInError()
    if result.get("error") or result.get("__xhs_error"):
        raise DirectMessageError(result.get("error") or result["__xhs_error"])
    return result


def _wait_ready(page: BridgePage, user_id: str = "", timeout: float = 45.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        state = _run(page, "snapshot")
        if state.get("ready") and (
            not user_id or (state.get("user_id") == user_id and state.get("conversation_ready"))
        ):
            return state
        if time.monotonic() >= deadline:
            raise DirectMessageError("私信页面未就绪，请检查登录、网页消息功能及收件人是否可访问")
        time.sleep(0.5)


def list_conversations(page: BridgePage, name: str = "") -> dict:
    """只列出网页当前已加载的单人会话，不读取消息正文。"""
    state = _run(page, "snapshot")
    if not state.get("on_chat_page"):
        page.navigate(CHAT_URL)
        page.wait_for_load()
    _wait_ready(page)
    result = _run(page, "list", name=name)
    return {"success": True, "scope": "loaded_conversations", **result}


def _open_recipient(page: BridgePage, user_id: str, expected_name: str) -> dict:
    if not re.fullmatch(r"[0-9a-fA-F]{24}", user_id):
        raise DirectMessageError("用户 ID 必须是主页或会话中的 24 位十六进制 ID")
    if not expected_name.strip():
        raise DirectMessageError("必须提供收件人的完整昵称用于交叉核对")
    state = _run(page, "snapshot")
    if state.get("user_id") != user_id:
        if state.get("draft"):
            raise DirectMessageError("当前会话存在草稿，请先处理，避免切换会话丢失内容")
        page.navigate(f"{CHAT_URL}/{user_id}")
        page.wait_for_load()
    _wait_ready(page, user_id)
    return _run(page, "check", user_id=user_id, expected_name=expected_name)


def fill_direct_message(page: BridgePage, user_id: str, expected_name: str, content: str) -> dict:
    """填写文字并返回预览，不触发发送。不同的已有草稿不会被覆盖。"""
    validate_content(content)
    _open_recipient(page, user_id, expected_name)
    _run(page, "fill", user_id=user_id, expected_name=expected_name, content=content)
    # Vue 的 input 处理可能异步更新；再次读取确认没有截断或切换会话。
    state = _run(page, "check", user_id=user_id, expected_name=expected_name)
    if state.get("draft") != content:
        raise DirectMessageError("输入框正文与预期不一致，未发送")
    return {
        "success": True,
        "status": "draft",
        "sent": False,
        "user_id": user_id,
        "recipient": expected_name,
        "content": content,
    }


def _is_acknowledged(message: dict) -> bool:
    store_id = str(message.get("store_id", ""))
    return (
        bool(message.get("message_id"))
        and store_id.isdigit()
        and int(store_id) > 0
        and not message.get("failed")
        and not message.get("pending")
    )


def send_direct_message(
    page: BridgePage,
    user_id: str,
    expected_name: str,
    content: str,
    *,
    confirmed: bool = False,
    timeout: float = 20.0,
) -> dict:
    """发送一次；发送后的异常、失败或超时均不自动重发。"""
    if not confirmed:
        raise DirectMessageError("发送私信需要 --confirm；仅预览请用 fill-direct-message")
    validate_content(content)
    state = _open_recipient(page, user_id, expected_name)
    outgoing = state.get("outgoing", [])
    if outgoing and outgoing[-1].get("text") == content:
        raise DirectMessageError("最近一条发出的私信与正文相同，已停止以避免重复发送；请核对会话")
    fill_direct_message(page, user_id, expected_name, content)
    base = {"user_id": user_id, "recipient": expected_name}
    # check 与 keydown 在同一次页面执行中完成，避免焦点切换后发给另一会话。
    try:
        submitted = _run(
            page, "send", user_id=user_id, expected_name=expected_name, content=content
        )
        before_ids = set(submitted["before_ids"])
        deadline = time.monotonic() + timeout
        while True:
            state = _run(page, "check", user_id=user_id, expected_name=expected_name)
            candidates = [
                message
                for message in state.get("outgoing", [])
                if message.get("message_id") not in before_ids and message.get("text") == content
            ]
            if len(candidates) == 1:
                message = candidates[0]
                if message.get("failed"):
                    return {
                        **base,
                        "success": False,
                        "status": "failed",
                        "error": "网页显示私信发送失败，未自动重试",
                        "message_id": message.get("message_id"),
                    }
                if _is_acknowledged(message) and not state.get("draft"):
                    return {
                        **base,
                        "success": True,
                        "status": "sent",
                        "message_id": message["message_id"],
                        "store_id": message["store_id"],
                        "verification": "server_message_id",
                    }
            if time.monotonic() >= deadline:
                break
            time.sleep(0.5)
    except Exception:
        # 桥接超时可能发生在网页已经发送之后，不把异常误报为安全可重试。
        return {
            **base,
            "success": False,
            "status": "unknown",
            "error": "发送期间连接或会话状态变化，结果未知；请核对会话，勿直接重试",
        }
    return {
        **base,
        "success": False,
        "status": "unknown",
        "error": "未在等待时间内确认服务端消息标识；请核对会话，勿直接重试",
    }
