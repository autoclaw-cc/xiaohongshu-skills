"""模拟人类浏览行为：通过搜索框搜索 → 分析页面结构 → 按顺序浏览笔记。

反风控设计：
- 不使用 URL 直接跳转，通过搜索框 UI 输入关键词
- 逐字键盘输入模拟真实打字
- 按从左到右、从上到下的自然阅读顺序浏览
- 先获取 DOM 结构再操作，避免盲目点击
- 所有操作带随机间隔
"""

from __future__ import annotations

import base64
import json
import logging
import random
import time
from pathlib import Path

from .cdp import Page
from .human import sleep_random, navigation_delay
from .urls import EXPLORE_URL

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = Path("/tmp/xhs/screenshots")

# ========== 选择器 ==========
SEARCH_INPUT = "input.search-input"
SEARCH_INPUT_ACTIVE = "#search-input"  # 搜索激活后的输入框

# 笔记卡片
FEED_CARD = "section.note-item"
FEED_CARD_COVER = "a.cover"

# 详情弹窗
DETAIL_NOTE_CONTAINER = "#noteContainer"
DETAIL_CLOSE = ".close-circle"


# ========== 搜索框交互 ==========


def _ensure_on_explore(page: Page) -> None:
    """确保当前在小红书首页。如果不在，导航过去。

    首页是正常的浏览器入口，允许直接导航。
    """
    current = page.evaluate("location.href") or ""
    if "xiaohongshu.com" not in current:
        logger.info("不在小红书，导航到首页")
        page.navigate(EXPLORE_URL)
        page.wait_for_load()
        navigation_delay()


def _search_via_ui(page: Page, keyword: str) -> None:
    """通过搜索框 UI 输入关键词并搜索（不使用 URL 跳转）。

    流程：
    1. 点击搜索框激活
    2. 逐字输入关键词（CDP 键盘事件）
    3. 按 Enter 提交搜索
    """
    _ensure_on_explore(page)
    sleep_random(500, 1000)

    # 点击搜索框
    logger.info("点击搜索框")
    page.click_element(SEARCH_INPUT)
    sleep_random(300, 600)

    # 等待搜索输入框激活
    time.sleep(0.5)

    # 清空可能存在的旧内容
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

    # 逐字输入关键词
    logger.info("输入关键词: %s", keyword)
    for char in keyword:
        page._send_session("Input.dispatchKeyEvent", {
            "type": "keyDown", "text": char,
        })
        page._send_session("Input.dispatchKeyEvent", {
            "type": "keyUp", "text": char,
        })
        sleep_random(50, 120)

    sleep_random(300, 600)

    # 按 Enter 搜索（完整三段式事件：rawKeyDown + char + keyUp）
    # 小红书 Vue 组件需要完整序列才能触发表单提交
    logger.info("按 Enter 提交搜索")
    page._send_session("Input.dispatchKeyEvent", {
        "type": "rawKeyDown", "key": "Enter", "code": "Enter",
        "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
    })
    page._send_session("Input.dispatchKeyEvent", {
        "type": "char", "key": "Enter", "code": "Enter",
        "windowsVirtualKeyCode": 13, "text": "\r",
    })
    page._send_session("Input.dispatchKeyEvent", {
        "type": "keyUp", "key": "Enter", "code": "Enter",
        "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
    })

    # 等待搜索结果加载
    page.wait_for_load()
    sleep_random(2000, 3500)
    page.wait_dom_stable()


# ========== 页面结构分析 ==========


def _analyze_feed_cards(page: Page) -> list[dict]:
    """分析当前页面的笔记卡片布局。

    返回按从左到右、从上到下排序的卡片列表，
    每个卡片包含位置、标题、索引等信息。
    """
    cards = page.evaluate("""
        (() => {
            const cards = document.querySelectorAll('section.note-item');
            return Array.from(cards).map((card, i) => {
                const rect = card.getBoundingClientRect();
                const link = card.querySelector('a.cover, a');
                const title = card.querySelector('.title span, .note-item .title');
                const author = card.querySelector('.author-wrapper .name, .author .name');
                const likes = card.querySelector('.like-wrapper .count, [class*="like"] .count');
                return {
                    index: i,
                    title: title?.innerText?.trim() || '',
                    author: author?.innerText?.trim() || '',
                    likes: likes?.innerText?.trim() || '',
                    href: link?.href || '',
                    top: Math.round(rect.top),
                    left: Math.round(rect.left),
                    bottom: Math.round(rect.bottom),
                    right: Math.round(rect.right),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                    visible: rect.top < window.innerHeight && rect.bottom > 0,
                    centerX: Math.round(rect.left + rect.width / 2),
                    centerY: Math.round(rect.top + rect.height / 2),
                };
            });
        })()
    """)
    if not isinstance(cards, list):
        return []

    # 按行列排序：先按 top 分组（容差 50px），组内按 left 排序
    cards.sort(key=lambda c: (c["top"] // 50, c["left"]))
    logger.info("分析页面: 共 %d 张卡片", len(cards))
    return cards


# ========== 相关性预筛选 ==========


def _is_card_relevant(card: dict, relevance_terms: list[str]) -> bool:
    """判断卡片标题是否与搜索意图相关。

    Args:
        card: 卡片信息字典（含 title 字段）。
        relevance_terms: 相关性关键词列表。标题中包含任意一个即为相关。

    Returns:
        True 如果相关或无法判断（标题为空/无筛选词），False 如果明显不相关。
    """
    if not relevance_terms:
        return True  # 未提供筛选词，不过滤

    title = card.get("title", "").strip().lower()
    if not title:
        return True  # 标题为空，无法判断，放行

    for term in relevance_terms:
        if term.lower() in title:
            return True

    return False


def _filter_relevant_cards(
    cards: list[dict],
    relevance_terms: list[str],
) -> tuple[list[dict], list[dict]]:
    """按相关性筛选卡片。

    Args:
        cards: 按顺序排列的卡片列表。
        relevance_terms: 相关性关键词列表。

    Returns:
        (relevant, skipped) 两个列表。
    """
    if not relevance_terms:
        return cards, []

    relevant = []
    skipped = []
    for card in cards:
        if _is_card_relevant(card, relevance_terms):
            relevant.append(card)
        else:
            skipped.append(card)

    logger.info(
        "相关性筛选: %d 相关 / %d 跳过 (共 %d)",
        len(relevant), len(skipped), len(cards),
    )
    if skipped:
        skipped_titles = [c.get("title", "?")[:20] for c in skipped[:5]]
        logger.debug("跳过: %s%s", skipped_titles, "..." if len(skipped) > 5 else "")

    return relevant, skipped


# ========== 详情页交互与信息提取 ==========


def _extract_detail_info(page: Page) -> dict:
    """从笔记详情弹窗中提取完整信息。"""
    info = page.evaluate("""
        (() => {
            const container = document.querySelector('#noteContainer, .note-detail-mask');
            if (!container) return null;

            // 标题
            const title = container.querySelector('#detail-title, .title')?.innerText?.trim() || '';

            // 正文
            const desc = container.querySelector('#detail-desc, .desc, .note-text');
            const content = desc?.innerText?.trim() || '';

            // 作者
            const author = container.querySelector('.author-wrapper .username, .author-container .username, [class*=author] .name')?.innerText?.trim() || '';

            // 互动数据
            const likeEl = container.querySelector('.like-wrapper .count, [class*="like-active"] ~ .count, .engage-bar .like .count, .engage-bar-style .like .count');
            const collectEl = container.querySelector('.collect-wrapper .count, .engage-bar .collect .count, .engage-bar-style .collect .count');
            const commentEl = container.querySelector('.chat-wrapper .count, .engage-bar .chat .count, .engage-bar-style .chat .count');

            // 图片列表
            const images = container.querySelectorAll('.swiper-slide img, .carousel img, .note-image img');
            const imageUrls = Array.from(images).map(img => img.src).filter(Boolean);

            // 标签
            const tags = container.querySelectorAll('.tag a, .hash-tag a, #hash-tag a');
            const tagTexts = Array.from(tags).map(t => t.innerText?.trim()).filter(Boolean);

            // 评论（前几条）
            const comments = container.querySelectorAll('.parent-comment, .comment-item');
            const commentList = Array.from(comments).slice(0, 5).map(c => {
                const cAuthor = c.querySelector('.name, .author')?.innerText?.trim() || '';
                const cContent = c.querySelector('.content, .text')?.innerText?.trim() || '';
                const cLikes = c.querySelector('.like .count, [class*=like] .count')?.innerText?.trim() || '';
                return {author: cAuthor, content: cContent, likes: cLikes};
            });

            return {
                title: title,
                content: content,
                author: author,
                likes: likeEl?.innerText?.trim() || '',
                collects: collectEl?.innerText?.trim() || '',
                comments_count: commentEl?.innerText?.trim() || '',
                image_urls: imageUrls,
                tags: tagTexts,
                top_comments: commentList,
            };
        })()
    """)
    return info if isinstance(info, dict) else {}


def _take_screenshot(page: Page, name: str) -> str:
    """全屏截图，返回文件路径。"""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    result = page._send_session("Page.captureScreenshot", {"format": "png"})
    data = base64.b64decode(result.get("data", ""))
    path = SCREENSHOT_DIR / f"{name}.png"
    path.write_bytes(data)
    logger.info("截图: %s (%d bytes)", path, len(data))
    return str(path)


def _smooth_scroll(page: Page, distance: int) -> None:
    """模拟人类平滑滚动。"""
    steps = random.randint(3, 6)
    step_distance = distance / steps
    for _ in range(steps):
        page.dispatch_wheel_event(step_distance + random.uniform(-20, 20))
        time.sleep(random.uniform(0.05, 0.15))


def _click_card_by_position(page: Page, card: dict) -> bool:
    """通过位置点击笔记卡片（模拟鼠标行为）。"""
    if not card.get("visible"):
        # 先滚动到可见
        page.scroll_nth_element_into_view(FEED_CARD, card["index"])
        sleep_random(500, 1000)

    # 重新获取位置（滚动后可能变化）
    new_pos = page.evaluate(f"""
        (() => {{
            const cards = document.querySelectorAll('section.note-item');
            const card = cards[{card['index']}];
            if (!card) return null;
            const rect = card.getBoundingClientRect();
            return {{
                centerX: rect.left + rect.width / 2,
                centerY: rect.top + rect.height / 2,
                visible: rect.top < window.innerHeight && rect.bottom > 0,
            }};
        }})()
    """)
    if not new_pos or not new_pos.get("visible"):
        return False

    x = new_pos["centerX"] + random.uniform(-20, 20)
    y = new_pos["centerY"] + random.uniform(-20, 20)

    # 悬停
    page.mouse_move(x, y)
    sleep_random(500, 2000)

    # 点击
    page.mouse_click(x, y)
    sleep_random(1000, 2000)
    return True


def _close_detail(page: Page) -> None:
    """关闭详情弹窗。"""
    closed = page.evaluate("""
        (() => {
            const btn = document.querySelector('.close-circle, .note-detail-mask .close, [class*=close-btn]');
            if (btn) { btn.click(); return 'clicked'; }
            return 'not_found';
        })()
    """)
    if closed == "not_found":
        page._send_session("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "Escape", "code": "Escape",
            "windowsVirtualKeyCode": 27,
        })
        page._send_session("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "Escape", "code": "Escape",
            "windowsVirtualKeyCode": 27,
        })
    sleep_random(500, 1000)


# ========== 主入口 ==========


def browse_keyword(
    page: Page,
    keyword: str,
    max_notes: int = 10,
    max_time: float = 300.0,
    on_note: callable = None,
    relevance_terms: list[str] | None = None,
) -> list[dict]:
    """以人类节奏浏览单个关键词搜索结果。"""
    return _browse_single(page, keyword, max_notes, max_time, on_note, screenshot_start=2, relevance_terms=relevance_terms)


def browse_keywords(
    page: Page,
    keywords: list[str],
    max_notes_per_keyword: int = 5,
    max_notes_total: int = 15,
    max_time: float = 600.0,
    on_note: callable = None,
    relevance_terms: list[str] | None = None,
) -> list[dict]:
    """以人类节奏浏览多个关键词的搜索结果（关键词泛化搜索）。

    对每个关键词执行搜索并浏览，去重后汇总结果。
    关键词之间模拟人类行为：返回首页 → 重新输入下一个关键词。

    Args:
        page: CDP 页面对象。
        keywords: 搜索关键词列表。
        max_notes_per_keyword: 每个关键词最多浏览的笔记数。
        max_notes_total: 所有关键词总共最多浏览的笔记数。
        max_time: 总最大浏览时间（秒）。
        on_note: 回调函数。
        relevance_terms: 相关性筛选词列表。卡片标题需包含至少一个词才会被浏览。

    Returns:
        去重后的浏览结果列表。
    """
    start_time = time.monotonic()
    all_notes = []
    all_screenshots = []
    seen_titles = set()
    screenshot_idx = 2

    # 清理旧截图
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for old in SCREENSHOT_DIR.glob("*.png"):
        old.unlink(missing_ok=True)

    for kw_idx, keyword in enumerate(keywords):
        # 检查总限制
        if len(all_notes) >= max_notes_total:
            logger.info("达到总笔记数限制 %d", max_notes_total)
            break
        elapsed = time.monotonic() - start_time
        if elapsed > max_time:
            logger.info("达到总时间限制 %.0fs", elapsed)
            break

        remaining_total = max_notes_total - len(all_notes)
        this_max = min(max_notes_per_keyword, remaining_total)

        logger.info("=== 关键词 %d/%d: '%s' (目标 %d 条) ===", kw_idx + 1, len(keywords), keyword, this_max)

        # 搜索
        _search_via_ui(page, keyword)

        # 截图搜索结果
        ss = _take_screenshot(page, f"{screenshot_idx:02d}_search_{kw_idx}")
        all_screenshots.append(ss)
        screenshot_idx += 1

        # 分析和浏览
        cards = _analyze_feed_cards(page)
        if not cards:
            logger.warning("关键词 '%s' 未找到结果", keyword)
            continue

        # 相关性预筛选
        if relevance_terms:
            cards, skipped = _filter_relevant_cards(cards, relevance_terms)
            if not cards:
                logger.warning("关键词 '%s' 无相关卡片（跳过 %d 张）", keyword, len(skipped))
                continue

        browsed_indices = set()
        notes_this_kw = 0

        for card in cards:
            if notes_this_kw >= this_max:
                break
            elapsed = time.monotonic() - start_time
            if elapsed > max_time or len(all_notes) >= max_notes_total:
                break
            if card["index"] in browsed_indices:
                continue

            browsed_indices.add(card["index"])

            # 去重：跳过已浏览过的标题
            card_title = card.get("title", "").strip()
            if card_title and card_title in seen_titles:
                logger.debug("跳过重复笔记: %s", card_title[:30])
                continue

            # 滚动使卡片可见
            if not card.get("visible"):
                _smooth_scroll(page, random.randint(300, 500))
                sleep_random(1000, 2000)

            # 点击
            logger.info("打开笔记 #%d: %s", card["index"], card_title[:30])
            if not _click_card_by_position(page, card):
                continue

            sleep_random(1500, 2500)

            # 提取详情
            info = _extract_detail_info(page)
            if not info:
                info = {"title": card_title, "author": card.get("author", "")}

            info["card_title"] = card_title
            info["card_index"] = card["index"]
            info["search_keyword"] = keyword

            # 去重检查（详情标题）
            detail_title = info.get("title", "").strip()
            if detail_title and detail_title in seen_titles:
                _close_detail(page)
                sleep_random(500, 1000)
                continue

            # 记录已见标题
            if card_title:
                seen_titles.add(card_title)
            if detail_title:
                seen_titles.add(detail_title)

            # 模拟阅读
            for _ in range(random.randint(2, 5)):
                _smooth_scroll(page, random.randint(150, 350))
                sleep_random(800, 2500)

            # 截图
            ss = _take_screenshot(page, f"{screenshot_idx:02d}_detail_{kw_idx}_{card['index']}")
            all_screenshots.append(ss)
            info["screenshot"] = ss
            screenshot_idx += 1

            all_notes.append(info)
            notes_this_kw += 1

            logger.info(
                "已浏览 %d (总 %d/%d): %s (by %s, 👍%s) [kw: %s]",
                notes_this_kw, len(all_notes), max_notes_total,
                (detail_title or card_title)[:30],
                info.get("author", "?"),
                info.get("likes", "?"),
                keyword,
            )

            if on_note:
                on_note(len(all_notes), info)

            # 关闭详情
            sleep_random(500, 1500)
            _close_detail(page)
            sleep_random(1000, 2500)

        # 关键词之间等待（模拟思考下一次搜索）
        if kw_idx < len(keywords) - 1:
            logger.info("切换到下一个关键词，等待中...")
            sleep_random(2000, 4000)

    # 最终截图
    ss = _take_screenshot(page, f"{screenshot_idx:02d}_final")
    all_screenshots.append(ss)

    elapsed = time.monotonic() - start_time
    logger.info(
        "多关键词浏览完成: %d 个关键词, %d 条笔记, %.0f 秒, %d 张截图",
        len(keywords), len(all_notes), elapsed, len(all_screenshots),
    )

    return all_notes


def _browse_single(
    page: Page,
    keyword: str,
    max_notes: int = 10,
    max_time: float = 300.0,
    on_note: callable = None,
    screenshot_start: int = 2,
    relevance_terms: list[str] | None = None,
) -> list[dict]:
    """单关键词浏览的内部实现。"""
    start_time = time.monotonic()
    browsed_notes = []
    screenshots = []

    # 清理旧截图
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for old in SCREENSHOT_DIR.glob("*.png"):
        old.unlink(missing_ok=True)

    # ===== 第一步：通过搜索框搜索 =====
    _search_via_ui(page, keyword)

    # 截图搜索结果页
    ss = _take_screenshot(page, "01_search_results")
    screenshots.append(ss)

    # ===== 第二步：分析页面结构 =====
    cards = _analyze_feed_cards(page)
    if not cards:
        logger.warning("未找到搜索结果卡片")
        return browsed_notes

    # 相关性预筛选
    if relevance_terms:
        cards, skipped = _filter_relevant_cards(cards, relevance_terms)
        if not cards:
            logger.warning("无相关卡片（跳过 %d 张）", len(skipped))
            return browsed_notes

    visible_cards = [c for c in cards if c.get("visible")]
    logger.info("可见卡片: %d/%d", len(visible_cards), len(cards))

    # ===== 第三步：按顺序浏览笔记 =====
    card_queue = list(cards)  # 已按从左到右、从上到下排序（已筛选）
    browsed_indices = set()
    screenshot_idx = 2

    for card in card_queue:
        # 检查限制
        if len(browsed_notes) >= max_notes:
            break
        elapsed = time.monotonic() - start_time
        if elapsed > max_time:
            logger.info("达到时间限制 %.0fs", elapsed)
            break
        if card["index"] in browsed_indices:
            continue

        browsed_indices.add(card["index"])

        # 如果卡片不可见，滚动使其可见
        if not card.get("visible"):
            _smooth_scroll(page, random.randint(300, 500))
            sleep_random(1000, 2000)
            # 刷新卡片列表获取新的可见卡片
            refreshed = _analyze_feed_cards(page)
            new_visible = [c for c in refreshed if c.get("visible") and c["index"] not in browsed_indices]
            if not new_visible:
                continue
            card = new_visible[0]
            browsed_indices.add(card["index"])

        # 点击卡片
        logger.info("打开笔记 #%d: %s", card["index"], card.get("title", "")[:30])
        if not _click_card_by_position(page, card):
            logger.warning("点击卡片失败: #%d", card["index"])
            continue

        # 等待详情弹窗加载
        sleep_random(1500, 2500)

        # 提取详情信息
        info = _extract_detail_info(page)
        if not info:
            info = {"title": card.get("title", ""), "author": card.get("author", "")}

        info["card_title"] = card.get("title", "")
        info["card_index"] = card["index"]

        # 模拟阅读（随机滚动详情页）
        scroll_times = random.randint(2, 5)
        for _ in range(scroll_times):
            _smooth_scroll(page, random.randint(150, 350))
            sleep_random(800, 2500)

        # 截图
        ss = _take_screenshot(page, f"{screenshot_idx:02d}_detail_{card['index']}")
        screenshots.append(ss)
        info["screenshot"] = ss
        screenshot_idx += 1

        browsed_notes.append(info)
        note_num = len(browsed_notes)
        logger.info(
            "已浏览 %d/%d: %s (by %s, 👍%s)",
            note_num, max_notes,
            info.get("title", "")[:30],
            info.get("author", "未知"),
            info.get("likes", "?"),
        )

        if on_note:
            on_note(note_num, info)

        # 关闭详情
        sleep_random(500, 1500)
        _close_detail(page)
        sleep_random(1000, 2500)

    # 最终截图
    ss = _take_screenshot(page, f"{screenshot_idx:02d}_final")
    screenshots.append(ss)

    elapsed = time.monotonic() - start_time
    logger.info("浏览完成: 共 %d 条笔记, 耗时 %.0f 秒, %d 张截图", len(browsed_notes), elapsed, len(screenshots))

    return browsed_notes
