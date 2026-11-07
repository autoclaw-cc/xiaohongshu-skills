"""Feed 详情 + 评论加载，对应 Go xiaohongshu/feed_detail.go（867 行）。"""

from __future__ import annotations

import json
import logging
import random
import re
import time
import urllib.parse

from .cdp import Page
from .errors import NoFeedDetailError, PageNotAccessibleError
from .human import (
    BUTTON_CLICK_INTERVAL,
    DEFAULT_MAX_ATTEMPTS,
    FINAL_SPRINT_PUSH_COUNT,
    HUMAN_DELAY,
    LARGE_SCROLL_TRIGGER,
    MAX_CLICK_PER_ROUND,
    MIN_SCROLL_DELTA,
    POST_SCROLL,
    READ_TIME,
    SCROLL_WAIT,
    SHORT_READ,
    STAGNANT_LIMIT,
    calculate_scroll_delta,
    get_scroll_interval,
    get_scroll_ratio,
    sleep_random,
)
from .selectors import (
    ACCESS_ERROR_WRAPPER,
    END_CONTAINER,
    NO_COMMENTS_TEXT,
    PARENT_COMMENT,
    SHOW_MORE_BUTTON,
)
from .types import (
    CommentList,
    CommentLoadConfig,
    FeedDetail,
    FeedDetailResponse,
)
from .urls import make_feed_detail_url

logger = logging.getLogger(__name__)

# 页面不可访问关键词
_INACCESSIBLE_KEYWORDS = [
    "当前笔记暂时无法浏览",
    "该内容因违规已被删除",
    "该笔记已被删除",
    "内容不存在",
    "笔记不存在",
    "已失效",
    "私密笔记",
    "仅作者可见",
    "因用户设置，你无法查看",
    "因违规无法查看",
    "Isn't Available",
    "isn't available",
]

# 扫码验证关键词（触发反爬机制）
_SCAN_QRCODE_KEYWORDS = [
    "扫码查看",
    "打开小红书App扫码",
    "请使用小红书App扫码",
]

_REPLY_COUNT_RE = re.compile(r"展开\s*(\d+)\s*条回复")
_TOTAL_COMMENT_RE = re.compile(r"共(\d+)条评论")


def get_feed_detail(
    page: Page,
    feed_id: str,
    xsec_token: str,
    load_all_comments: bool = False,
    config: CommentLoadConfig | None = None,
    keyword: str = "篮球",
) -> FeedDetailResponse:
    """获取 Feed 详情（含评论）。

    Args:
        page: CDP 页面对象。
        feed_id: Feed ID。
        xsec_token: xsec_token。
        load_all_comments: 是否加载全部评论。
        config: 评论加载配置。

    Raises:
        PageNotAccessibleError: 页面不可访问。
        NoFeedDetailError: 未获取到详情数据。
    """
    if config is None:
        config = CommentLoadConfig()

    url = make_feed_detail_url(feed_id, xsec_token)
    logger.info("打开 feed 详情页: %s", url)
    logger.info(
        "配置: 点击更多=%s, 回复阈值=%d, 最大评论数=%d, 滚动速度=%s",
        config.click_more_replies,
        config.max_replies_threshold,
        config.max_comment_items,
        config.scroll_speed,
    )

    # 导航（含重试）
    # 注：风控 302→/404 由扩展 auto-fix（自动从搜索页换新 token 重试），
    # 此处只捕获真正的瞬态错误（网络超时等），风控拦截不重试。
    for attempt in range(3):
        try:
            page.navigate(url)
            page.wait_for_load()
            page.wait_dom_stable()
            break
        except Exception as e:
            err_str = str(e)
            # 风控 302 重定向：扩展已尝试 auto-fix，仍失败说明 token/session 问题，直接抛出
            if "风控拦截" in err_str or "重定向至" in err_str:
                raise PageNotAccessibleError(f"笔记无法访问（风控/token）: {err_str}") from e
            logger.debug("页面导航重试 #%d: %s", attempt, e)
            time.sleep(0.5 + random.random())
    else:
        raise RuntimeError("页面导航失败")

    sleep_random(800, 1500)

    # 模拟阅读鼠标轨迹（同步进行，增加行为真实性）
    try:
        page.simulate_reading_mouse(random.randint(2000, 4000))
    except Exception:
        pass

    # 检查页面可访问性（扫码验证时自动等待重试）
    _check_page_accessible(page, url, keyword)

    # 加载全部评论
    if load_all_comments:
        try:
            _load_all_comments(page, config)
        except Exception as e:
            logger.warning("加载全部评论失败: %s", e)

    return _extract_feed_detail(page, feed_id)


# ========== 页面检查 ==========


def _check_page_accessible(page: Page, url: str = "", keyword: str = "篮球") -> None:
    """检查页面是否可访问。

    风控绕过策略（三级重试）：
      1. 先回首页，再跳目标页（same-origin 导航）
      2. 若仍触发：在搜索结果页加载后再跳目标页（模拟真实搜索→点击笔记流程）
      3. 仍失败则抛出异常，提示用户手动扫码
    """
    time.sleep(0.5)

    text = page.get_element_text(ACCESS_ERROR_WRAPPER)
    if not text:
        return

    text = text.strip()

    if _is_scan_qrcode_verification(text) and url:
        # ── Level 1：首页跳转 ──
        logger.warning("触发小红书扫码验证，Level-1 重试：先回首页再访问目标...")
        time.sleep(10)
        page.navigate("https://www.xiaohongshu.com")
        page.wait_for_load()
        page.wait_dom_stable()
        time.sleep(2 + random.random())
        page.navigate(url)
        page.wait_for_load()
        page.wait_dom_stable()
        time.sleep(1)

        retry_text = page.get_element_text(ACCESS_ERROR_WRAPPER)
        if not retry_text or not retry_text.strip():
            logger.info("Level-1 重试成功，继续加载笔记")
            return
        if not _is_scan_qrcode_verification(retry_text.strip()):
            text = retry_text.strip()
        else:
            # ── Level 2：搜索跳转 ──
            search_url = (
                "https://www.xiaohongshu.com/search_result?"
                + urllib.parse.urlencode({"keyword": keyword, "type": "51"})
            )
            logger.warning(
                "Level-1 失败，Level-2 重试：搜索「%s」后再访问目标...", keyword
            )
            time.sleep(5 + random.random() * 5)
            page.navigate(search_url)
            page.wait_for_load()
            page.wait_dom_stable()
            time.sleep(3 + random.random() * 2)
            page.navigate(url)
            page.wait_for_load()
            page.wait_dom_stable()
            time.sleep(1)

            retry_text = page.get_element_text(ACCESS_ERROR_WRAPPER)
            if not retry_text or not retry_text.strip():
                logger.info("Level-2 重试成功，继续加载笔记")
                return
            if _is_scan_qrcode_verification(retry_text.strip()):
                raise PageNotAccessibleError(
                    "触发了小红书验证，需要在浏览器中扫码完成验证后重试。"
                    "这通常是小红书的反爬机制，请稍后再试或在 Chrome 中手动打开该笔记完成验证"
                )
            text = retry_text.strip()

    for kw in _INACCESSIBLE_KEYWORDS:
        if kw in text:
            raise PageNotAccessibleError(kw)

    if text:
        raise PageNotAccessibleError(text)


def _is_scan_qrcode_verification(text: str) -> bool:
    """判断页面文本是否为扫码验证。"""
    return any(kw in text for kw in _SCAN_QRCODE_KEYWORDS)


# ========== 数据提取 ==========


_EXTRACT_STATE_JS = """
(() => {
    if (window.__INITIAL_STATE__ &&
        window.__INITIAL_STATE__.note &&
        window.__INITIAL_STATE__.note.noteDetailMap) {
        return JSON.stringify(window.__INITIAL_STATE__.note.noteDetailMap);
    }
    return "";
})()
"""

_EXTRACT_DOM_BODY_JS = """
(() => {
    const bodyEl = document.querySelector('#detail-desc');
    if (!bodyEl) return null;
    // 先提取话题标签
    const tags = Array.from(bodyEl.querySelectorAll('a.tag'))
                      .map(a => a.textContent.trim())
                      .filter(Boolean);
    // 克隆后移除 a.tag，再取 textContent（不依赖 CSS layout，后台标签页同样生效）
    const clone = bodyEl.cloneNode(true);
    clone.querySelectorAll('a.tag').forEach(el => el.remove());
    const bodyText = clone.textContent.replace(/\\n{3,}/g, '\\n\\n').trim();
    if (!bodyText && !tags.length) return null;
    return {body: bodyText, tags: tags};
})()
"""


def _extract_feed_detail(page: Page, feed_id: str) -> FeedDetailResponse:
    """从 __INITIAL_STATE__ 提取 Feed 详情，轮询最多 10s。

    两阶段提取：
    1. 等待 __INITIAL_STATE__ 数据就绪（最多 10s）
    2. 等待 #detail-desc DOM 渲染完成后提取正文（最多 8s）
       Vue 渲染时序：state 早于 DOM，需分开轮询。
    """
    # 阶段 1：等待 __INITIAL_STATE__
    deadline = time.monotonic() + 10.0
    result = None
    while time.monotonic() < deadline:
        result = page.evaluate(_EXTRACT_STATE_JS)
        if result:
            break
        time.sleep(0.3)

    if not result:
        raise NoFeedDetailError()

    note_detail_map = json.loads(result)
    note_data = note_detail_map.get(feed_id)
    if not note_data:
        raise NoFeedDetailError()

    # 阶段 2：等待 #detail-desc 渲染（Vue 异步渲染，state 就绪后 DOM 可能还未填充）
    dom_deadline = time.monotonic() + 8.0
    dom_result = None
    while time.monotonic() < dom_deadline:
        dom_result = page.evaluate(_EXTRACT_DOM_BODY_JS)
        if dom_result and isinstance(dom_result, dict) and dom_result.get("body", "").strip():
            break
        time.sleep(0.3)

    note = note_data.get("note", {})
    if dom_result and isinstance(dom_result, dict):
        note["_domBody"] = dom_result.get("body", "")
        note["_domTags"] = dom_result.get("tags", [])

    return FeedDetailResponse(
        note=FeedDetail.from_dict(note),
        comments=CommentList.from_dict(note_data.get("comments", {})),
    )


# ========== 评论加载状态机 ==========


def _load_all_comments(page: Page, config: CommentLoadConfig) -> None:
    """加载全部评论的状态机。"""
    max_attempts = (
        config.max_comment_items * 3 if config.max_comment_items > 0 else DEFAULT_MAX_ATTEMPTS
    )
    scroll_interval = get_scroll_interval(config.scroll_speed)

    logger.info("开始加载评论...")
    _scroll_to_comments_area(page)
    sleep_random(*HUMAN_DELAY)

    # 检查是否无评论
    if _check_no_comments(page):
        logger.info("检测到无评论区域，跳过加载")
        return

    # 状态
    last_count = 0
    last_scroll_top = 0
    stagnant_checks = 0
    total_clicked = 0
    total_skipped = 0

    for attempt in range(max_attempts):
        logger.debug("=== 尝试 %d/%d ===", attempt + 1, max_attempts)

        # 一次调用同时获取：评论数 + 是否到底
        state = _check_page_state(page)

        if state["at_end"]:
            logger.info(
                "检测到 THE END，加载完成: %d 条评论, 点击: %d, 跳过: %d",
                state["count"],
                total_clicked,
                total_skipped,
            )
            return

        # 定期点击展开按钮
        if config.click_more_replies and attempt % BUTTON_CLICK_INTERVAL == 0:
            clicked, skipped = _click_show_more_buttons(page, config.max_replies_threshold)
            total_clicked += clicked
            total_skipped += skipped
            if clicked > 0 or skipped > 0:
                sleep_random(*READ_TIME)
                # 第二轮
                c2, s2 = _click_show_more_buttons(page, config.max_replies_threshold)
                total_clicked += c2
                total_skipped += s2
                if c2 > 0 or s2 > 0:
                    sleep_random(*SHORT_READ)

        current_count = state["count"]
        if current_count != last_count:
            logger.info("评论增加: %d -> %d", last_count, current_count)
            last_count = current_count
            stagnant_checks = 0
        else:
            stagnant_checks += 1

        # 检查是否达到目标
        if config.max_comment_items > 0 and current_count >= config.max_comment_items:
            logger.info("已达到目标评论数: %d/%d", current_count, config.max_comment_items)
            return

        # 滚动
        if current_count > 0:
            _scroll_to_last_comment(page)
            sleep_random(*POST_SCROLL)

        large_mode = stagnant_checks >= LARGE_SCROLL_TRIGGER
        push_count = 1
        if large_mode:
            push_count = 3 + random.randint(0, 2)

        scroll_delta, current_scroll_top = _human_scroll(
            page, config.scroll_speed, large_mode, push_count
        )

        if scroll_delta < MIN_SCROLL_DELTA or current_scroll_top == last_scroll_top:
            stagnant_checks += 1
        else:
            stagnant_checks = 0
            last_scroll_top = current_scroll_top

        # 停滞处理
        if stagnant_checks >= STAGNANT_LIMIT:
            logger.info("停滞过多，尝试大冲刺...")
            _human_scroll(page, config.scroll_speed, True, 10)
            stagnant_checks = 0

        time.sleep(scroll_interval)

    # 最终冲刺
    logger.info("达到最大尝试次数，最后冲刺...")
    _human_scroll(page, config.scroll_speed, True, FINAL_SPRINT_PUSH_COUNT)
    count = _get_comment_count(page)
    logger.info("加载结束: %d 条评论, 点击: %d, 跳过: %d", count, total_clicked, total_skipped)


# ========== 滚动 ==========


def _human_scroll(
    page: Page,
    speed: str,
    large_mode: bool,
    push_count: int,
) -> tuple[int, int]:
    """人类化滚动。

    Returns:
        (actual_delta, current_scroll_top)
    """
    # 一次 evaluate 同时获取 scrollTop 和 viewportHeight，减少 bridge 往返
    state = page.evaluate(
        "({scrollTop: window.pageYOffset || document.documentElement.scrollTop || 0,"
        " viewportHeight: window.innerHeight})"
    )
    before_top = int(state.get("scrollTop", 0)) if isinstance(state, dict) else page.get_scroll_top()
    viewport_height = int(state.get("viewportHeight", 768)) if isinstance(state, dict) else page.get_viewport_height()

    base_ratio = get_scroll_ratio(speed)
    if large_mode:
        base_ratio *= 2.0

    actual_delta = 0
    current_scroll_top = before_top

    for i in range(max(1, push_count)):
        scroll_delta = calculate_scroll_delta(viewport_height, base_ratio)
        page.scroll_by(0, int(scroll_delta))
        sleep_random(*SCROLL_WAIT)

        current_scroll_top = page.get_scroll_top()
        delta_this = current_scroll_top - before_top
        actual_delta += delta_this
        before_top = current_scroll_top

        if i < push_count - 1:
            sleep_random(*HUMAN_DELAY)

    # 如果没有滚动，强制到底部
    if actual_delta < MIN_SCROLL_DELTA and push_count > 0:
        page.scroll_to_bottom()
        sleep_random(*POST_SCROLL)
        current_scroll_top = page.get_scroll_top()
        actual_delta = current_scroll_top - (before_top - actual_delta)

    return actual_delta, current_scroll_top


def _scroll_to_comments_area(page: Page) -> None:
    """滚动到评论区。"""
    logger.info("滚动到评论区...")
    page.scroll_element_into_view(".comments-container")
    time.sleep(0.5)
    # 触发懒加载
    page.dispatch_wheel_event(100)


def _scroll_to_last_comment(page: Page) -> None:
    """滚动到最后一条评论（count + scroll 合并为 1 次 evaluate）。"""
    sel = json.dumps(PARENT_COMMENT)
    page.evaluate(
        f"(function(){{const els=document.querySelectorAll({sel});"
        f"if(els.length>0)els[els.length-1].scrollIntoView({{block:'center',behavior:'smooth'}})}})()"
    )


# ========== DOM 查询 ==========


def _get_comment_count(page: Page) -> int:
    """获取当前评论数量。"""
    return page.get_elements_count(PARENT_COMMENT)


def _get_total_comment_count(page: Page) -> int:
    """获取总评论数（从 "共N条评论" 提取）。"""
    text = page.get_element_text(".comments-container .total")
    if not text:
        return 0
    match = _TOTAL_COMMENT_RE.search(text)
    if match:
        return int(match.group(1))
    return 0


def _check_no_comments(page: Page) -> bool:
    """检查是否无评论区域。"""
    text = page.get_element_text(NO_COMMENTS_TEXT)
    if not text:
        return False
    return "这是一片荒地" in text.strip()


def _check_end_container(page: Page) -> bool:
    """检查是否到达底部 THE END。"""
    text = page.get_element_text(END_CONTAINER)
    if not text:
        return False
    upper = text.strip().upper()
    return "THE END" in upper or "THEEND" in upper


def _check_page_state(page: Page) -> dict:
    """一次 evaluate 同时获取评论数、是否无评论、是否到达底部。

    Returns:
        {"count": int, "no_comments": bool, "at_end": bool}
    """
    sel_parent = json.dumps(PARENT_COMMENT)
    sel_no = json.dumps(NO_COMMENTS_TEXT)
    sel_end = json.dumps(END_CONTAINER)
    result = page.evaluate(
        f"(function(){{"
        f"  var count = document.querySelectorAll({sel_parent}).length;"
        f"  var noText = (document.querySelector({sel_no}) || {{}}).textContent || '';"
        f"  var endText = ((document.querySelector({sel_end}) || {{}}).textContent || '').toUpperCase();"
        f"  return {{count: count,"
        f"    no_comments: noText.indexOf('这是一片荒地') >= 0,"
        f"    at_end: endText.indexOf('THE END') >= 0 || endText.indexOf('THEEND') >= 0}};"
        f"}})()"
    )
    if isinstance(result, dict):
        return result
    return {"count": 0, "no_comments": False, "at_end": False}


# ========== 按钮点击 ==========


def _click_show_more_buttons(page: Page, max_threshold: int) -> tuple[int, int]:
    """点击"展开N条回复"按钮。

    优化：1 次 evaluate 批量获取所有按钮文本，每次点击用 1 次 evaluate 完成
    scroll+click，减少 bridge 往返次数。

    Returns:
        (clicked, skipped)
    """
    sel = json.dumps(SHOW_MORE_BUTTON)

    # 一次 evaluate 获取所有按钮文本
    texts = page.evaluate(
        f"Array.from(document.querySelectorAll({sel})).map(e => e.textContent || '')"
    )
    if not texts or not isinstance(texts, list):
        return 0, 0

    max_click = MAX_CLICK_PER_ROUND + random.randint(0, MAX_CLICK_PER_ROUND - 1)
    clicked = 0
    skipped = 0

    for i, text in enumerate(texts):
        if clicked >= max_click:
            break

        if not text:
            continue

        # 检查是否应该跳过
        if max_threshold > 0:
            match = _REPLY_COUNT_RE.search(text)
            if match:
                reply_count = int(match.group(1))
                if reply_count > max_threshold:
                    logger.debug(
                        "跳过 '%s'（回复数 %d > 阈值 %d）", text, reply_count, max_threshold
                    )
                    skipped += 1
                    continue

        # 一次 evaluate 完成：滚动到按钮 + 点击
        page.evaluate(
            f"(function(){{"
            f"  var el=document.querySelectorAll({sel})[{i}];"
            f"  if(el){{el.scrollIntoView({{block:'center',behavior:'smooth'}});el.click();}}"
            f"}})()"
        )
        sleep_random(*READ_TIME)
        clicked += 1

    return clicked, skipped
