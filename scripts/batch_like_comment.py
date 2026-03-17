"""批量搜索、点赞并收集帖子信息（用于后续评论）。

用法:
    python batch_like_comment.py --keywords "法律AI,AI工程师 招聘" \
        --max-notes 10 --max-per-keyword 5
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

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
logger = logging.getLogger("batch_like_comment")


def _extract_feed_url_info(page: Page) -> dict:
    """从当前弹窗中提取 feed_id 和 xsec_token。"""
    result = page.evaluate('''
        (() => {
            // 从弹窗内的链接或当前 URL 提取
            const url = location.href;
            const match = url.match(/explore\\/([a-f0-9]+)/);
            const tokenMatch = url.match(/xsec_token=([^&]+)/);
            
            // 也尝试从 noteContainer 的 data 属性提取
            const container = document.querySelector("#noteContainer");
            const noteId = container?.getAttribute("data-note-id") || "";
            
            return {
                feed_id: match ? match[1] : noteId,
                xsec_token: tokenMatch ? tokenMatch[1] : "",
                url: url,
            };
        })()
    ''')
    return result or {}


def batch_search_like(
    page: Page,
    keywords: list[str],
    filter_terms: list[str],
    max_notes: int = 10,
    max_per_keyword: int = 5,
) -> list[dict]:
    results = []
    total = 0
    seen_titles = set()

    for ki, kw in enumerate(keywords):
        if total >= max_notes:
            break

        logger.info("=== 关键词 %d/%d: '%s' ===", ki + 1, len(keywords), kw)

        _ensure_on_explore(page)
        try:
            _search_via_ui(page, kw)
        except Exception as e:
            logger.warning("搜索失败: %s - %s", kw, e)
            continue

        sleep_random(2000, 3000)

        cards = _analyze_feed_cards(page)
        logger.info("找到 %d 张卡片", len(cards))

        if filter_terms:
            relevant, _ = _filter_relevant_cards(cards, filter_terms)
        else:
            relevant = cards

        logger.info("相关卡片: %d", len(relevant))

        liked_this_kw = 0
        for card in relevant:
            if total >= max_notes or liked_this_kw >= max_per_keyword:
                break

            title = card.get("title", "")
            # 去重
            if title and title in seen_titles:
                continue
            if title:
                seen_titles.add(title)

            logger.info("打开: %s", title[:60] or "(无标题)")

            try:
                if not _click_card_by_position(page, card):
                    logger.warning("点击卡片失败，跳过")
                    continue

                sleep_random(2000, 3000)

                # 提取详情
                detail = _extract_detail_info(page) or {}
                url_info = _extract_feed_url_info(page)

                # 点赞
                like_result = like_feed_in_popup(page)
                logger.info(
                    "点赞: %s - %s (by %s)",
                    "✅" if like_result.success else "❌",
                    like_result.message,
                    detail.get("author", ""),
                )

                results.append({
                    "index": total + 1,
                    "keyword": kw,
                    "title": detail.get("title", title)[:80],
                    "content": detail.get("content", "")[:200],
                    "author": detail.get("author", ""),
                    "likes": detail.get("likes", ""),
                    "comments_count": detail.get("comments_count", ""),
                    "like_success": like_result.success,
                    "like_message": like_result.message,
                    "feed_id": url_info.get("feed_id", ""),
                    "xsec_token": url_info.get("xsec_token", ""),
                })

                total += 1
                liked_this_kw += 1

                _close_detail(page)
                sleep_random(1500, 3000)

            except Exception as e:
                logger.warning("处理卡片异常: %s，跳过", e)
                try:
                    _close_detail(page)
                except Exception:
                    pass
                sleep_random(1000, 2000)
                continue

        if ki < len(keywords) - 1:
            sleep_random(2000, 4000)

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keywords", required=True)
    parser.add_argument("--filter", default="")
    parser.add_argument("--max-notes", type=int, default=10)
    parser.add_argument("--max-per-keyword", type=int, default=5)
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    filter_terms = [f.strip() for f in args.filter.split(",") if f.strip()]

    browser = Browser()
    page = browser.get_or_create_page()

    try:
        results = batch_search_like(
            page, keywords, filter_terms,
            max_notes=args.max_notes,
            max_per_keyword=args.max_per_keyword,
        )
        output = {
            "success": True,
            "total_liked": sum(1 for r in results if r["like_success"]),
            "total": len(results),
            "results": results,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        try:
            browser.close_page(page)
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
