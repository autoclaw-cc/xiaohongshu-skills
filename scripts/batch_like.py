"""批量搜索并点赞脚本。

用法:
    python batch_like.py --keywords "大叔,爹系男友,女大,地陪" \
        --filter "大叔,爹系,男友,女大,地陪" --max-notes 10
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

# 确保能导入 xhs 包
sys.path.insert(0, ".")

from xhs.browse import (
    _click_card_by_position,
    _close_detail,
    _extract_detail_info,
    _analyze_feed_cards,
    _filter_relevant_cards,
    _search_via_ui,
    _ensure_on_explore,
)
from xhs.cdp import Browser, Page
from xhs.human import sleep_random
from xhs.like_favorite import like_feed_in_popup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("batch_like")


def _is_relevant(title: str, filter_terms: list[str]) -> bool:
    if not filter_terms:
        return True
    title_lower = title.lower()
    return any(t.lower() in title_lower for t in filter_terms)


def batch_search_and_like(
    page: Page,
    keywords: list[str],
    filter_terms: list[str],
    max_notes: int = 10,
    max_per_keyword: int = 5,
) -> list[dict]:
    """搜索多个关键词，对每条相关笔记点赞。"""
    results = []
    total_liked = 0

    for ki, kw in enumerate(keywords):
        if total_liked >= max_notes:
            break

        logger.info("=== 关键词 %d/%d: '%s' ===", ki + 1, len(keywords), kw)

        # 搜索
        _ensure_on_explore(page)
        try:
            _search_via_ui(page, kw)
        except Exception as e:
            logger.warning("搜索失败: %s - %s", kw, e)
            continue

        sleep_random(2000, 3000)

        # 获取卡片
        cards = _analyze_feed_cards(page)
        logger.info("找到 %d 张卡片", len(cards))

        # 筛选相关卡片
        relevant, skipped = _filter_relevant_cards(cards, filter_terms)
        logger.info("相关卡片: %d / 跳过: %d", len(relevant), len(skipped))

        liked_this_kw = 0
        for card in relevant:
            if total_liked >= max_notes or liked_this_kw >= max_per_keyword:
                break

            title = card.get("title", "")

            logger.info("打开: %s", title[:50] or "(无标题)")

            # 点击卡片打开弹窗
            if not _click_card_by_position(page, card):
                logger.warning("点击卡片失败，跳过")
                continue

            sleep_random(2000, 3000)

            # 提取详情
            detail = _extract_detail_info(page) or {}
            note_title = detail.get("title", title)
            author = detail.get("author", "")

            # 点赞
            like_result = like_feed_in_popup(page)
            logger.info(
                "点赞结果: %s - %s (by %s)",
                "✅" if like_result.success else "❌",
                like_result.message,
                author,
            )

            results.append({
                "keyword": kw,
                "title": note_title[:60],
                "author": author,
                "like_success": like_result.success,
                "like_message": like_result.message,
            })

            if like_result.success:
                total_liked += 1
                liked_this_kw += 1

            # 关闭弹窗
            _close_detail(page)
            sleep_random(1500, 3000)

        # 关键词之间间隔
        if ki < len(keywords) - 1:
            sleep_random(2000, 4000)

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keywords", required=True, help="逗号分隔的关键词")
    parser.add_argument("--filter", default="", help="逗号分隔的筛选词")
    parser.add_argument("--max-notes", type=int, default=10)
    parser.add_argument("--max-per-keyword", type=int, default=4)
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    filter_terms = [f.strip() for f in args.filter.split(",") if f.strip()]

    browser = Browser()
    page = browser.get_or_create_page()

    try:
        results = batch_search_and_like(
            page, keywords, filter_terms,
            max_notes=args.max_notes,
            max_per_keyword=args.max_per_keyword,
        )
        output = {
            "success": True,
            "total_liked": sum(1 for r in results if r["like_success"]),
            "total_attempted": len(results),
            "results": results,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        browser.close_page(page)
        browser.close()


if __name__ == "__main__":
    main()
