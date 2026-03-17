"""搜索 Feeds，对应 Go xiaohongshu/search.go。"""

from __future__ import annotations

import json
import logging
import time

from .cdp import Page
from .errors import NoFeedsError
from .human import sleep_random
from .selectors import FILTER_BUTTON, FILTER_PANEL
from .types import Feed, FilterOption
from .urls import make_search_url

logger = logging.getLogger(__name__)

# 筛选选项映射表：{筛选组索引: [(标签索引, 文本), ...]}
_FILTER_OPTIONS: dict[int, list[tuple[int, str]]] = {
    1: [(1, "综合"), (2, "最新"), (3, "最多点赞"), (4, "最多评论"), (5, "最多收藏")],
    2: [(1, "不限"), (2, "视频"), (3, "图文")],
    3: [(1, "不限"), (2, "一天内"), (3, "一周内"), (4, "半年内")],
    4: [(1, "不限"), (2, "已看过"), (3, "未看过"), (4, "已关注")],
    5: [(1, "不限"), (2, "同城"), (3, "附近")],
}

# 从 __INITIAL_STATE__ 提取搜索结果的 JS
_EXTRACT_SEARCH_JS = """
(() => {
    if (window.__INITIAL_STATE__ &&
        window.__INITIAL_STATE__.search &&
        window.__INITIAL_STATE__.search.feeds) {
        const feeds = window.__INITIAL_STATE__.search.feeds;
        const feedsData = feeds.value !== undefined ? feeds.value : feeds._value;
        if (feedsData) {
            return JSON.stringify(feedsData);
        }
    }
    return "";
})()
"""


def _find_internal_option(group_index: int, text: str) -> tuple[int, int]:
    """查找内部筛选选项索引。

    Returns:
        (filters_index, tags_index)

    Raises:
        ValueError: 未找到匹配的选项。
    """
    options = _FILTER_OPTIONS.get(group_index)
    if not options:
        raise ValueError(f"筛选组 {group_index} 不存在")

    for tags_index, option_text in options:
        if option_text == text:
            return group_index, tags_index

    valid = [t for _, t in options]
    raise ValueError(f"在筛选组 {group_index} 中未找到 '{text}'，有效值: {valid}")


def _convert_filters(filter_opt: FilterOption) -> list[tuple[int, int]]:
    """将 FilterOption 转换为内部 (filters_index, tags_index) 列表。"""
    result: list[tuple[int, int]] = []

    if filter_opt.sort_by:
        result.append(_find_internal_option(1, filter_opt.sort_by))
    if filter_opt.note_type:
        result.append(_find_internal_option(2, filter_opt.note_type))
    if filter_opt.publish_time:
        result.append(_find_internal_option(3, filter_opt.publish_time))
    if filter_opt.search_scope:
        result.append(_find_internal_option(4, filter_opt.search_scope))
    if filter_opt.location:
        result.append(_find_internal_option(5, filter_opt.location))

    return result


def search_feeds(
    page: Page,
    keyword: str,
    filter_option: FilterOption | None = None,
) -> list[Feed]:
    """搜索 Feeds。

    优先使用搜索框 UI 交互方式搜索（反风控）。
    如果搜索框不可用，回退到 URL 方式。

    Args:
        page: CDP 页面对象。
        keyword: 搜索关键词。
        filter_option: 可选筛选条件。

    Raises:
        NoFeedsError: 没有捕获到搜索结果。
        ValueError: 筛选选项无效。
    """
    # 优先通过搜索框 UI 搜索（反风控）
    if _search_via_searchbox(page, keyword):
        logger.info("通过搜索框 UI 完成搜索")
    else:
        # 回退到 URL 方式
        logger.warning("搜索框不可用，回退到 URL 方式")
        search_url = make_search_url(keyword)
        page.navigate(search_url)
    page.wait_for_load()
    page.wait_dom_stable()

    # 等待 __INITIAL_STATE__ 初始化
    _wait_for_initial_state(page)

    # 应用筛选条件
    if filter_option:
        internal_filters = _convert_filters(filter_option)
        if internal_filters:
            _apply_filters(page, internal_filters)

    # 提取搜索结果
    result = page.evaluate(_EXTRACT_SEARCH_JS)
    if not result:
        raise NoFeedsError()

    feeds_data = json.loads(result)
    return [Feed.from_dict(f) for f in feeds_data]


def _search_via_searchbox(page: Page, keyword: str) -> bool:
    """通过搜索框 UI 输入关键词搜索（反风控）。

    Returns:
        True 成功，False 搜索框不可用。
    """
    # 确保在小红书页面
    current = page.evaluate("location.href") or ""
    if "xiaohongshu.com" not in current:
        page.navigate("https://www.xiaohongshu.com/explore")
        page.wait_for_load()
        sleep_random(1000, 2000)

    # 查找搜索框
    has_search = page.has_element("input.search-input, #search-input")
    if not has_search:
        return False

    # 点击搜索框
    page.click_element("input.search-input")
    sleep_random(300, 600)

    # 清空
    page._send_session("Input.dispatchKeyEvent", {
        "type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 2,
    })
    page._send_session("Input.dispatchKeyEvent", {
        "type": "keyUp", "key": "a", "code": "KeyA", "modifiers": 2,
    })
    page._send_session("Input.dispatchKeyEvent", {
        "type": "keyDown", "key": "Backspace", "code": "Backspace",
        "windowsVirtualKeyCode": 8,
    })
    page._send_session("Input.dispatchKeyEvent", {
        "type": "keyUp", "key": "Backspace", "code": "Backspace",
        "windowsVirtualKeyCode": 8,
    })
    sleep_random(200, 400)

    # 逐字输入
    import random as _random
    for char in keyword:
        page._send_session("Input.dispatchKeyEvent", {"type": "keyDown", "text": char})
        page._send_session("Input.dispatchKeyEvent", {"type": "keyUp", "text": char})
        time.sleep(_random.uniform(0.05, 0.12))

    sleep_random(300, 600)

    # Enter
    page._send_session("Input.dispatchKeyEvent", {
        "type": "keyDown", "key": "Enter", "code": "Enter",
        "windowsVirtualKeyCode": 13,
    })
    page._send_session("Input.dispatchKeyEvent", {
        "type": "keyUp", "key": "Enter", "code": "Enter",
        "windowsVirtualKeyCode": 13,
    })

    sleep_random(2000, 3000)
    return True


def _wait_for_initial_state(page: Page, timeout: float = 10.0) -> None:
    """等待 __INITIAL_STATE__ 就绪。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready = page.evaluate("window.__INITIAL_STATE__ !== undefined")
        if ready:
            return
        time.sleep(0.5)
    logger.warning("等待 __INITIAL_STATE__ 超时")


def _apply_filters(page: Page, filters: list[tuple[int, int]]) -> None:
    """应用筛选条件。"""
    # 悬停筛选按钮
    page.hover_element(FILTER_BUTTON)

    # 等待筛选面板出现
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if page.has_element(FILTER_PANEL):
            break
        sleep_random(300, 600)

    # 点击各筛选项
    for filters_index, tags_index in filters:
        selector = (
            f"div.filter-panel div.filters:nth-child({filters_index}) "
            f"div.tags:nth-child({tags_index})"
        )
        page.click_element(selector)
        sleep_random(300, 600)

    # 等待页面更新
    page.wait_dom_stable()
    _wait_for_initial_state(page)
