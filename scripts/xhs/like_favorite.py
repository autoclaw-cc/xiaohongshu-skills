"""点赞/收藏操作 — 反风控版本。

通过 UI 点击弹窗内的 engage bar 按钮，不使用 URL 导航。

关键发现：
- `like-active` class 是 engage bar 按钮的默认 class，不代表已点赞
- 真正的点赞状态通过 SVG `xlink:href` 判断：`#like` = 未点赞，`#liked` = 已点赞
- 收藏状态：`#collect` = 未收藏，`#collected` = 已收藏
- engage bar 在 #noteContainer 内的底部
"""

from __future__ import annotations

import json
import logging
import random
import time

from .cdp import Page
from .human import sleep_random
from .types import ActionResult
from .urls import make_feed_detail_url

logger = logging.getLogger(__name__)


def _find_engage_button(page: Page, button_type: str = "like") -> dict | None:
    """在 #noteContainer 的 engage bar 中精确定位点赞/收藏按钮。

    Args:
        page: CDP 页面对象。
        button_type: "like" 或 "collect"。

    Returns:
        {x, y, liked, count, href} 或 None。
    """
    wrapper_cls = f"{button_type}-wrapper"
    # 已点赞/已收藏时 SVG href 变为 #liked / #collected
    liked_href = f"#{button_type}d" if button_type == "like" else "#collected"
    normal_href = f"#{button_type}"

    result = page.evaluate(f'''
        (() => {{
            function getCls(e) {{ return typeof e.className === "string" ? e.className : (e.className?.baseVal || ""); }}

            // 在 #noteContainer 内查找 engage bar
            const container = document.querySelector("#noteContainer");
            if (!container) return {{ found: false, error: "no noteContainer" }};

            // engage bar 在 interact-container / engage-bar-style 中
            const bars = container.querySelectorAll(".interact-container, .engage-bar-style, .engage-bar-container");
            for (const bar of bars) {{
                const btn = bar.querySelector(".{wrapper_cls}");
                if (!btn) continue;

                const r = btn.getBoundingClientRect();
                if (r.width < 5) continue;

                const svg = btn.querySelector("svg use");
                const href = svg?.getAttribute("xlink:href") || svg?.getAttribute("href") || "";
                const count = btn.querySelector(".count")?.innerText?.trim() || "";

                return {{
                    found: true,
                    source: "noteContainer-engage",
                    x: r.left + r.width / 2,
                    y: r.top + r.height / 2,
                    w: r.width,
                    h: r.height,
                    href: href,
                    liked: href === "{liked_href}",
                    count: count,
                    cls: getCls(btn).substring(0, 100),
                }};
            }}

            // 回退: 在整个 noteContainer 中找所有 wrapper，选 y 最大的
            const wrappers = container.querySelectorAll(".{wrapper_cls}");
            let best = null;
            let bestY = -1;
            for (const btn of wrappers) {{
                const r = btn.getBoundingClientRect();
                if (r.width > 5 && r.top > 0 && r.bottom < window.innerHeight) {{
                    if (r.y > bestY) {{
                        bestY = r.y;
                        best = btn;
                    }}
                }}
            }}
            if (best) {{
                const r = best.getBoundingClientRect();
                const svg = best.querySelector("svg use");
                const href = svg?.getAttribute("xlink:href") || svg?.getAttribute("href") || "";
                const count = best.querySelector(".count")?.innerText?.trim() || "";
                return {{
                    found: true,
                    source: "noteContainer-fallback",
                    x: r.left + r.width / 2,
                    y: r.top + r.height / 2,
                    w: r.width, h: r.height,
                    href: href,
                    liked: href === "{liked_href}",
                    count: count,
                    cls: getCls(best).substring(0, 100),
                }};
            }}

            return {{ found: false, error: "no {wrapper_cls} in noteContainer" }};
        }})()
    ''')

    if result and result.get("found"):
        status = "已" if result["liked"] else "未"
        logger.info(
            "%s 按钮: %s%s, count=%s, href=%s, source=%s, pos=(%d,%d)",
            button_type, status, button_type,
            result["count"], result["href"], result["source"],
            result["x"], result["y"],
        )
        return result

    logger.warning("未找到 %s 按钮: %s", button_type, result.get("error", "unknown"))
    return None


def _click_button(page: Page, btn: dict) -> None:
    """人类化点击按钮。"""
    x = btn["x"] + random.uniform(-3, 3)
    y = btn["y"] + random.uniform(-3, 3)
    page.mouse_move(x, y)
    sleep_random(300, 600)
    page.mouse_click(x, y)


# ========== 点赞 ==========


def like_feed_in_popup(page: Page) -> ActionResult:
    """在当前详情弹窗中点赞帖子。

    通过 SVG xlink:href 判断状态：#like = 未点赞，#liked = 已点赞。
    幂等：已点赞则跳过。

    Returns:
        ActionResult。
    """
    btn = _find_engage_button(page, "like")
    if not btn:
        return ActionResult(feed_id="", success=False, message="未找到点赞按钮")

    # 幂等：已点赞（href=#liked）则跳过
    if btn["liked"]:
        logger.info("已点赞（%s），跳过", btn["count"])
        return ActionResult(feed_id="", success=True, message=f"已点赞（{btn['count']}）")

    # 点击点赞
    logger.info("执行点赞: count=%s", btn["count"])
    _click_button(page, btn)
    sleep_random(2000, 3000)

    # 验证
    btn_after = _find_engage_button(page, "like")
    if btn_after and btn_after["liked"]:
        logger.info("点赞成功: %s → %s", btn["count"], btn_after["count"])
        return ActionResult(
            feed_id="", success=True,
            message=f"点赞成功（{btn['count']} → {btn_after['count']}）",
        )
    else:
        # 重试一次
        logger.warning("点赞未生效，重试")
        _click_button(page, btn)
        sleep_random(2000, 3000)
        btn_retry = _find_engage_button(page, "like")
        if btn_retry and btn_retry["liked"]:
            return ActionResult(feed_id="", success=True, message=f"点赞成功（重试, {btn_retry['count']}）")
        return ActionResult(feed_id="", success=False, message="点赞可能未成功")


def unlike_feed_in_popup(page: Page) -> ActionResult:
    """在当前详情弹窗中取消点赞。"""
    btn = _find_engage_button(page, "like")
    if not btn:
        return ActionResult(feed_id="", success=False, message="未找到点赞按钮")

    if not btn["liked"]:
        logger.info("未点赞，无需取消")
        return ActionResult(feed_id="", success=True, message="未点赞，无需取消")

    _click_button(page, btn)
    sleep_random(2000, 3000)

    btn_after = _find_engage_button(page, "like")
    if btn_after and not btn_after["liked"]:
        return ActionResult(feed_id="", success=True, message="取消点赞成功")
    return ActionResult(feed_id="", success=False, message="取消点赞可能未成功")


# ========== 收藏 ==========


def favorite_feed_in_popup(page: Page) -> ActionResult:
    """在当前详情弹窗中收藏帖子。"""
    btn = _find_engage_button(page, "collect")
    if not btn:
        return ActionResult(feed_id="", success=False, message="未找到收藏按钮")

    if btn["liked"]:  # liked field reused for collected status
        logger.info("已收藏（%s），跳过", btn["count"])
        return ActionResult(feed_id="", success=True, message=f"已收藏（{btn['count']}）")

    _click_button(page, btn)
    sleep_random(2000, 3000)

    btn_after = _find_engage_button(page, "collect")
    if btn_after and btn_after["liked"]:
        return ActionResult(feed_id="", success=True, message=f"收藏成功（{btn_after['count']}）")
    return ActionResult(feed_id="", success=False, message="收藏可能未成功")


def unfavorite_feed_in_popup(page: Page) -> ActionResult:
    """在当前详情弹窗中取消收藏。"""
    btn = _find_engage_button(page, "collect")
    if not btn:
        return ActionResult(feed_id="", success=False, message="未找到收藏按钮")

    if not btn["liked"]:
        return ActionResult(feed_id="", success=True, message="未收藏，无需取消")

    _click_button(page, btn)
    sleep_random(2000, 3000)

    btn_after = _find_engage_button(page, "collect")
    if btn_after and not btn_after["liked"]:
        return ActionResult(feed_id="", success=True, message="取消收藏成功")
    return ActionResult(feed_id="", success=False, message="取消收藏可能未成功")


# ========== 包装函数：导航到详情页再操作 ==========

_DETAIL_INDICATOR = "#noteContainer .engage-bar"


def _navigate_to_detail(page: Page, feed_id: str, xsec_token: str) -> bool:
    """导航到笔记详情页并等待 engage bar 加载。"""
    url = make_feed_detail_url(feed_id, xsec_token)
    page.navigate(url)
    for attempt in range(8):
        if page.has_element(_DETAIL_INDICATOR):
            return True
        if page.has_element(".engage-bar"):
            return True
        logger.info("等待详情页渲染 (尝试 %d/8)...", attempt + 1)
        sleep_random(1500, 2500)
    return False


def like_feed(page: Page, feed_id: str, xsec_token: str) -> ActionResult:
    """导航到笔记详情页并点赞。"""
    if not _navigate_to_detail(page, feed_id, xsec_token):
        return ActionResult(feed_id=feed_id, success=False, message="详情页加载失败")
    result = like_feed_in_popup(page)
    result.feed_id = feed_id
    return result


def unlike_feed(page: Page, feed_id: str, xsec_token: str) -> ActionResult:
    """导航到笔记详情页并取消点赞。"""
    if not _navigate_to_detail(page, feed_id, xsec_token):
        return ActionResult(feed_id=feed_id, success=False, message="详情页加载失败")
    result = unlike_feed_in_popup(page)
    result.feed_id = feed_id
    return result


def favorite_feed(page: Page, feed_id: str, xsec_token: str) -> ActionResult:
    """导航到笔记详情页并收藏。"""
    if not _navigate_to_detail(page, feed_id, xsec_token):
        return ActionResult(feed_id=feed_id, success=False, message="详情页加载失败")
    result = favorite_feed_in_popup(page)
    result.feed_id = feed_id
    return result


def unfavorite_feed(page: Page, feed_id: str, xsec_token: str) -> ActionResult:
    """导航到笔记详情页并取消收藏。"""
    if not _navigate_to_detail(page, feed_id, xsec_token):
        return ActionResult(feed_id=feed_id, success=False, message="详情页加载失败")
    result = unfavorite_feed_in_popup(page)
    result.feed_id = feed_id
    return result
