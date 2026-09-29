"""搜索 Feeds，对应 Go xiaohongshu/search.go。"""

from __future__ import annotations

import json
import logging
import time

from .cdp import Page
from .errors import NoFeedsError
from .types import Feed, FilterOption
from .urls import make_search_url

logger = logging.getLogger(__name__)

# 筛选选项映射表：{筛选组索引: [文本, ...]}
_FILTER_OPTIONS: dict[int, list[str]] = {
    1: ["综合", "最新", "最多点赞", "最多评论", "最多收藏"],
    2: ["不限", "视频", "图文"],
    3: ["不限", "一天内", "一周内", "半年内"],
    4: ["不限", "已看过", "未看过", "已关注"],
    5: ["不限", "同城", "附近"],
}

# 从 __INITIAL_STATE__ 提取搜索结果的 JS
_EXTRACT_SEARCH_JS = """
(() => {
    const s = window.__INITIAL_STATE__?.search;
    if (!s?.feeds) return "";
    const data = s.feeds.value !== undefined ? s.feeds.value : s.feeds._value;
    return data ? JSON.stringify(data) : "";
})()
"""


def _find_internal_option(group_index: int, text: str) -> tuple[int, str]:
    """查找内部筛选选项。

    Returns:
        (filters_index, text)

    Raises:
        ValueError: 未找到匹配的选项。
    """
    options = _FILTER_OPTIONS.get(group_index)
    if not options:
        raise ValueError(f"筛选组 {group_index} 不存在")

    if text in options:
        return group_index, text

    raise ValueError(f"在筛选组 {group_index} 中未找到 '{text}'，有效值: {options}")


def _convert_filters(filter_opt: FilterOption) -> list[tuple[int, str]]:
    """将 FilterOption 转换为内部 (filters_index, text) 列表。"""
    result: list[tuple[int, str]] = []

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

    Args:
        page: CDP 页面对象。
        keyword: 搜索关键词。
        filter_option: 可选筛选条件。

    Raises:
        NoFeedsError: 没有捕获到搜索结果。
        ValueError: 筛选选项无效。
    """
    search_url = make_search_url(keyword)
    page.navigate(search_url)
    page.wait_for_load()
    page.wait_dom_stable()

    # 等待 __INITIAL_STATE__.search.feeds 有数据
    _wait_for_search_feeds(page)

    # 应用筛选条件（若有）
    if filter_option:
        internal_filters = _convert_filters(filter_option)
        if internal_filters:
            _apply_filters(page, internal_filters)

    # 提取搜索结果
    result = page.evaluate(_EXTRACT_SEARCH_JS)
    if not result:
        raise NoFeedsError()

    feeds_data = json.loads(result)
    if not feeds_data:
        raise NoFeedsError()

    return [Feed.from_dict(f) for f in feeds_data]


def _wait_for_search_feeds(page: Page, timeout: float = 15.0) -> None:
    """等待 __INITIAL_STATE__.search.feeds 有数据。

    Raises:
        NoFeedsError: 超时仍无数据。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = page.evaluate(_EXTRACT_SEARCH_JS)
        if result:
            try:
                if json.loads(result):
                    return
            except json.JSONDecodeError:
                pass
        time.sleep(0.3)
    raise NoFeedsError()


def _apply_filters(page: Page, filters: list[tuple[int, str]]) -> None:
    """应用筛选条件。

    在单次 evaluate 调用内完成：点击筛选按钮 → 等待面板 → 按文本点击各选项
    → 等待搜索结果刷新。
    避免多次 WebSocket 连接导致面板关闭的时序问题。
    """
    filter_js_list = ", ".join(f"[{idx}, {json.dumps(text)}]" for idx, text in filters)

    # 记录当前 feeds 快照，用于检测结果是否已刷新
    snapshot_js = (
        "JSON.stringify(window.__INITIAL_STATE__?.search?.feeds?.value ?? "
        "window.__INITIAL_STATE__?.search?.feeds?._value ?? null)"
    )

    script = f"""
(() => {{
  return new Promise((resolve, reject) => {{
    const btn = document.querySelector('div.filter');
    if (!btn) {{ reject('筛选按钮不存在'); return; }}

    // 记录点击前的 feeds 快照（用于判断结果已刷新）
    const snapshot = {snapshot_js};
    btn.click();

    const items = [{filter_js_list}];
    let attempts = 0;

    // 等待筛选面板出现
    const panelTimer = setInterval(() => {{
      const wrapper = document.querySelector('div.filters-wrapper');
      if (!wrapper) {{
        if (++attempts > 50) {{ clearInterval(panelTimer); reject('筛选面板等待超时'); }}
        return;
      }}
      clearInterval(panelTimer);

      // 依次点击各筛选项
      for (const [groupIdx, text] of items) {{
        const group = wrapper.querySelectorAll('div.filters')[groupIdx - 1];
        if (!group) {{ reject('筛选组 ' + groupIdx + ' 不存在'); return; }}
        const tag = Array.from(group.querySelectorAll('div.tags'))
          .find(el => el.textContent.trim() === text);
        if (!tag) {{ reject('选项不存在: ' + text); return; }}
        tag.click();
      }}

      // 等待搜索结果刷新（feeds 快照变化）
      let refreshAttempts = 0;
      const refreshTimer = setInterval(() => {{
        const current = {snapshot_js};
        if (current !== snapshot) {{
          clearInterval(refreshTimer);
          resolve(null);
          return;
        }}
        if (++refreshAttempts > 60) {{
          // 超时也继续（结果可能未变化）
          clearInterval(refreshTimer);
          resolve(null);
        }}
      }}, 100);
    }}, 100);
  }});
}})()
"""
    try:
        page.evaluate(script)
    except Exception as e:
        raise ValueError(f"应用筛选失败: {e}") from e

    # 等待 __INITIAL_STATE__ 中有新数据
    _wait_for_search_feeds(page)
