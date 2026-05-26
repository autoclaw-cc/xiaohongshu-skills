"""关注/取消关注操作。"""

from __future__ import annotations

import logging

from .cdp import Page
from .feed_detail import _check_page_accessible
from .human import sleep_random
from .selectors import FOLLOW_BUTTON_TEXT_FOLLOWED, FOLLOW_BUTTON_WRAPPER
from .types import ActionResult
from .urls import make_feed_detail_url

logger = logging.getLogger(__name__)


def _prepare_page(page: Page, feed_id: str, xsec_token: str) -> None:
    """导航到 feed 详情页并校验可访问。"""
    url = make_feed_detail_url(feed_id, xsec_token)
    logger.info("打开 feed 详情页进行关注: %s", url)

    page.navigate(url)
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(800, 1500)
    _check_page_accessible(page)


def follow_feed(page: Page, feed_id: str, xsec_token: str) -> ActionResult:
    """关注笔记作者（幂等：已关注则跳过）。"""
    _prepare_page(page, feed_id, xsec_token)
    return _toggle_follow(page, feed_id, target_followed=True)


def unfollow_feed(page: Page, feed_id: str, xsec_token: str) -> ActionResult:
    """取消关注笔记作者（幂等：未关注则跳过）。"""
    _prepare_page(page, feed_id, xsec_token)
    return _toggle_follow(page, feed_id, target_followed=False)


def _toggle_follow(page: Page, feed_id: str, target_followed: bool) -> ActionResult:
    """执行关注/取消关注操作。"""
    action_name = "关注" if target_followed else "取消关注"

    current_text = _get_follow_button_text(page)
    if not current_text:
        raise RuntimeError("未找到关注按钮，可能已失效或网页结构变更")

    followed = _is_followed(current_text)
    if followed == target_followed:
        logger.info("feed %s 已%s，跳过（按钮文案：%s）", feed_id, action_name, current_text)
        return ActionResult(feed_id=feed_id, success=True, message=f"已{action_name}")

    page.click_element(FOLLOW_BUTTON_WRAPPER)
    sleep_random(1000, 1800)

    latest_text = _get_follow_button_text(page)
    if latest_text:
        latest_followed = _is_followed(latest_text)
        if latest_followed == target_followed:
            logger.info("feed %s %s成功，当前按钮文案: %s", feed_id, action_name, latest_text)
            return ActionResult(feed_id=feed_id, success=True, message=f"{action_name}成功")

    logger.warning("feed %s %s后状态未达预期，重试一次", feed_id, action_name)
    page.click_element(FOLLOW_BUTTON_WRAPPER)
    sleep_random(900, 1600)
    return ActionResult(feed_id=feed_id, success=True, message=f"{action_name}已执行")


def _is_followed(button_text: str) -> bool:
    """根据按钮文案判断是否已关注。"""
    normalized = button_text.strip().replace(" ", "")
    # 仅“已关注”判定为已关注，避免误判其它文案
    return normalized == FOLLOW_BUTTON_TEXT_FOLLOWED


def _get_follow_button_text(page: Page) -> str:
    text = page.evaluate(
        f"""
        (() => {{
            const btn = document.querySelector({FOLLOW_BUTTON_WRAPPER!r});
            if (!btn) return "";
            const textEl = btn.querySelector("span.reds-button-new-text");
            const txt = (textEl ? textEl.textContent : btn.textContent) || "";
            return txt.trim();
        }})()
        """
    )
    return str(text or "").strip()
