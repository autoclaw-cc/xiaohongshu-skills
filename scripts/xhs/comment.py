"""评论操作，对应 Go xiaohongshu/comment_feed.go。"""

from __future__ import annotations

import json
import logging
import time

from .cdp import Page
from .feed_detail import _check_end_container, _check_page_accessible, _get_comment_count
from .human import sleep_random
from .selectors import (
    COMMENT_INPUT_FIELD,
    COMMENT_INPUT_TRIGGER,
    COMMENT_SUBMIT_BUTTON,
    PARENT_COMMENT,
    REPLY_BUTTON,
)
from .urls import make_feed_detail_url

logger = logging.getLogger(__name__)

# 详情页关键元素——用于确认详情页已渲染
_DETAIL_INDICATOR = "#noteContainer .engage-bar"


def _ensure_detail_loaded(page: Page, feed_id: str) -> bool:
    """确认详情页 DOM 已渲染（engage-bar 存在）。

    小红书 SPA 导航到详情 URL 后，有时会在同一 tab 中以 modal
    形式呈现详情页，也可能新开 tab。此函数只检查当前 page 是否
    已渲染出详情页的关键 DOM 元素。

    Returns:
        True 表示详情页已就绪。
    """
    for attempt in range(6):
        if page.has_element(_DETAIL_INDICATOR):
            return True
        if page.has_element(".engage-bar"):
            return True
        logger.info("等待详情页渲染 (尝试 %d/6)...", attempt + 1)
        sleep_random(2000, 3000)
    return False


def _switch_to_detail_tab(page: Page, feed_id: str, host: str = "127.0.0.1", port: int = 9222) -> Page | None:
    """如果当前 tab 未渲染详情页，尝试从其他 tab 中找到匹配的详情页。

    通过 CDP /json 枚举所有 tab，找到 URL 包含 feed_id 的 tab 并连接。

    Returns:
        渲染了详情页的 Page，或 None。
    """
    import requests as _req

    base_url = f"http://{host}:{port}"
    try:
        targets = _req.get(f"{base_url}/json", timeout=5).json()
    except Exception:
        return None

    for target in targets:
        if target.get("type") != "page":
            continue
        url = target.get("url", "")
        if f"explore/{feed_id}" not in url:
            continue
        target_id = target["id"]
        if target_id == page.target_id:
            continue  # 跳过当前 tab
        try:
            result = page._cdp.send(
                "Target.attachToTarget",
                {"targetId": target_id, "flatten": True},
            )
            session_id = result.get("sessionId")
            if not session_id:
                continue
            other_page = Page(page._cdp, target_id, session_id)
            other_page._send_session("Page.enable")
            other_page._send_session("DOM.enable")
            other_page._send_session("Runtime.enable")
        except Exception:
            continue
        if other_page.has_element(".engage-bar"):
            logger.info("切换到详情 tab: %s", target_id[:20])
            return other_page
    return None


def _insert_text_and_enable(page: Page, selector: str, content: str) -> None:
    """通过 CDP Input.insertText 向 contentEditable 元素输入文本。

    与 keyDown/keyUp 逐字输入不同，insertText 能正确触发
    Vue / React 的 input 事件，使提交按钮从 disabled 变为 enabled。
    """
    # Focus 并清空
    page.evaluate(
        f"""(() => {{
            const el = document.querySelector({json.dumps(selector)});
            if (!el) return;
            el.focus();
            // 全选 + 删除（清空已有内容）
            const sel = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(el);
            sel.removeAllRanges();
            sel.addRange(range);
            document.execCommand('delete', false);
        }})()"""
    )
    time.sleep(0.2)

    # 通过 CDP insertText 输入（会正确触发 inputEvent）
    page._send_session("Input.insertText", {"text": content})
    time.sleep(0.3)

    # 额外触发 input 事件，确保 Vue 响应式
    page.evaluate(
        f"""(() => {{
            const el = document.querySelector({json.dumps(selector)});
            if (el) {{
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('compositionend', {{bubbles: true}}));
            }}
        }})()"""
    )


def _wait_submit_enabled(page: Page, timeout: float = 5.0) -> bool:
    """等待提交按钮变为可点击状态。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        disabled = page.evaluate(
            """(() => {
                const btn = document.querySelector('.engage-bar button.submit')
                           || document.querySelector('div.bottom button.submit');
                return btn ? btn.disabled : true;
            })()"""
        )
        if not disabled:
            return True
        time.sleep(0.5)
    return False


def _verify_comment_posted(page: Page, content: str, timeout: float = 8.0) -> bool:
    """提交后验证评论是否出现在评论列表中。

    检查方式：检测评论区中是否出现包含评论内容片段的新元素。
    """
    snippet = content[:20]  # 取前 20 字作为匹配片段
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = page.evaluate(
            f"""(() => {{
                const comments = document.querySelectorAll(
                    '.parent-comment .content, .comment-item .content, .comment .content'
                );
                for (const c of comments) {{
                    if (c.textContent.includes({json.dumps(snippet)})) return true;
                }}
                return false;
            }})()"""
        )
        if found:
            return True
        time.sleep(1)
    return False


def post_comment(page: Page, feed_id: str, xsec_token: str, content: str) -> None:
    """发表评论到 Feed。

    Args:
        page: CDP 页面对象。
        feed_id: Feed ID。
        xsec_token: xsec_token。
        content: 评论内容。

    Raises:
        RuntimeError: 评论失败。
    """
    url = make_feed_detail_url(feed_id, xsec_token)
    logger.info("打开 feed 详情页: %s", url)

    # 使用 JS 导航（而非 CDP Page.navigate），保持 SPA 上下文
    page.evaluate(f"window.location.href = {json.dumps(url)}")
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(2000, 3000)

    # —— 修复：确认详情页在当前 tab 渲染，否则切到正确 tab ——
    if not _ensure_detail_loaded(page, feed_id):
        logger.warning("当前 tab 未渲染详情页，尝试查找其他 tab")
        other = _switch_to_detail_tab(page, feed_id)
        if other is None:
            raise RuntimeError("详情页未加载，无法评论")
        page = other
        if not _ensure_detail_loaded(page, feed_id):
            raise RuntimeError("切换 tab 后详情页仍未渲染")

    _check_page_accessible(page)

    # —— 点击评论触发区域 ——
    # 优先点击 engage-bar 中的触发 span（占位符 "说点什么..."）
    engage_trigger = ".engage-bar .input-box .content-edit span"
    fallback_trigger = COMMENT_INPUT_TRIGGER  # div.input-box div.content-edit span

    for _attempt in range(3):
        if page.has_element(engage_trigger) or page.has_element(fallback_trigger):
            break
        logger.info("等待评论输入框出现 (尝试 %d/3)...", _attempt + 1)
        sleep_random(3000, 5000)

    if page.has_element(engage_trigger):
        page.click_element(engage_trigger)
    elif page.has_element(fallback_trigger):
        page.click_element(fallback_trigger)
    else:
        raise RuntimeError("未找到评论输入框，该帖子可能不支持评论或网页端不可访问")
    sleep_random(400, 800)

    # —— 输入评论内容（修复：使用 insertText 替代 keyDown/keyUp）——
    input_sel = "#content-textarea"
    if not page.has_element(input_sel):
        input_sel = COMMENT_INPUT_FIELD  # 回退到原选择器

    page.wait_for_element(input_sel, timeout=5)
    _insert_text_and_enable(page, input_sel, content)
    sleep_random(600, 1200)

    # —— 验证提交按钮已启用 ——
    if not _wait_submit_enabled(page):
        # 再次尝试触发 input 事件
        page.evaluate(
            f"""(() => {{
                const el = document.querySelector({json.dumps(input_sel)});
                if (el) {{
                    el.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: el.innerText}}));
                }}
            }})()"""
        )
        sleep_random(500, 1000)
        if not _wait_submit_enabled(page, timeout=3):
            raise RuntimeError("提交按钮未启用，评论内容可能未正确输入")

    # —— 记录提交前评论数 ——
    pre_count = _get_comment_count(page)

    # —— 点击提交 ——
    submit_sel = ".engage-bar button.submit"
    if not page.has_element(submit_sel):
        submit_sel = COMMENT_SUBMIT_BUTTON
    page.click_element(submit_sel)
    sleep_random(1500, 2500)

    # —— 提交后验证 ——
    if _verify_comment_posted(page, content):
        logger.info("评论已确认出现在评论区: feed=%s", feed_id)
    else:
        # 再用评论数变化做二次检查
        post_count = _get_comment_count(page)
        if post_count > pre_count:
            logger.info("评论数增加 (%d → %d)，评论应已发送: feed=%s", pre_count, post_count, feed_id)
        else:
            logger.warning(
                "未能验证评论是否成功（评论数 %d → %d），请手动检查: feed=%s",
                pre_count, post_count, feed_id,
            )
            raise RuntimeError(
                f"评论提交后未检测到新评论（评论数 {pre_count} → {post_count}），可能被风控拦截"
            )


def reply_comment(
    page: Page,
    feed_id: str,
    xsec_token: str,
    content: str,
    comment_id: str = "",
    user_id: str = "",
) -> None:
    """回复指定评论。

    通过 comment_id 或 user_id 定位评论，然后回复。

    Args:
        page: CDP 页面对象。
        feed_id: Feed ID。
        xsec_token: xsec_token。
        content: 回复内容。
        comment_id: 评论 ID（优先使用）。
        user_id: 用户 ID（备选）。

    Raises:
        RuntimeError: 回复失败。
    """
    if not comment_id and not user_id:
        raise ValueError("comment_id 和 user_id 至少提供一个")

    url = make_feed_detail_url(feed_id, xsec_token)
    logger.info("打开 feed 详情页进行回复: %s", url)

    # 使用 JS 导航，保持 SPA 上下文
    page.evaluate(f"window.location.href = {json.dumps(url)}")
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(800, 1500)

    # 确认详情页渲染
    if not _ensure_detail_loaded(page, feed_id):
        other = _switch_to_detail_tab(page, feed_id)
        if other:
            page = other
        if not _ensure_detail_loaded(page, feed_id):
            raise RuntimeError("详情页未加载，无法回复")

    _check_page_accessible(page)
    sleep_random(1500, 2500)

    # 查找目标评论
    comment_found = _find_and_scroll_to_comment(page, comment_id, user_id)
    if not comment_found:
        raise RuntimeError(f"未找到评论 (commentID: {comment_id}, userID: {user_id})")

    sleep_random(800, 1500)

    # 点击回复按钮
    reply_selector = f"#comment-{comment_id} {REPLY_BUTTON}" if comment_id else REPLY_BUTTON
    page.click_element(reply_selector)
    sleep_random(800, 1500)

    # 输入回复内容（使用 insertText）
    input_sel = "#content-textarea"
    if not page.has_element(input_sel):
        input_sel = COMMENT_INPUT_FIELD
    page.wait_for_element(input_sel, timeout=5)
    _insert_text_and_enable(page, input_sel, content)
    sleep_random(600, 1200)

    # 验证按钮启用
    if not _wait_submit_enabled(page):
        raise RuntimeError("回复提交按钮未启用")

    # 点击提交
    submit_sel = ".engage-bar button.submit"
    if not page.has_element(submit_sel):
        submit_sel = COMMENT_SUBMIT_BUTTON
    page.click_element(submit_sel)
    sleep_random(1500, 2500)

    logger.info("回复评论成功")


def _find_and_scroll_to_comment(
    page: Page,
    comment_id: str,
    user_id: str,
    max_attempts: int = 100,
) -> bool:
    """查找并滚动到目标评论。"""
    logger.info("开始查找评论 - commentID: %s, userID: %s", comment_id, user_id)

    # 先滚动到评论区
    page.scroll_element_into_view(".comments-container")
    sleep_random(800, 1500)

    last_count = 0
    stagnant = 0

    for attempt in range(max_attempts):
        # 检查是否到底
        if _check_end_container(page):
            logger.info("已到达评论底部，未找到目标评论")
            break

        # 停滞检测
        current_count = _get_comment_count(page)
        if current_count != last_count:
            last_count = current_count
            stagnant = 0
        else:
            stagnant += 1
        if stagnant >= 10:
            logger.info("评论数量停滞超过10次")
            break

        # 滚动到最后一条评论
        if current_count > 0:
            page.scroll_nth_element_into_view(PARENT_COMMENT, current_count - 1)
            sleep_random(200, 500)

        # 继续滚动
        page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
        sleep_random(400, 800)

        # 通过 commentID 查找
        if comment_id:
            selector = f"#comment-{comment_id}"
            if page.has_element(selector):
                logger.info("通过 commentID 找到评论 (尝试 %d 次)", attempt + 1)
                page.scroll_element_into_view(selector)
                return True

        # 通过 userID 查找
        if user_id:
            found = page.evaluate(
                f"""
                (() => {{
                    const els = document.querySelectorAll(
                        '.parent-comment, .comment-item, .comment'
                    );
                    for (const el of els) {{
                        if (el.querySelector('[data-user-id="{user_id}"]')) {{
                            el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                            return true;
                        }}
                    }}
                    return false;
                }})()
                """
            )
            if found:
                logger.info("通过 userID 找到评论 (尝试 %d 次)", attempt + 1)
                return True

        sleep_random(600, 1200)

    return False


def _js_str(s: str) -> str:
    """将 Python 字符串转为 JS 字面量（含引号）。"""
    return json.dumps(s)
