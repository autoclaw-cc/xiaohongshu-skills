"""全自动营销脚本：搜索 → 点赞 → AI生成评论 → 自动发送。

⚠️ 警告：全自动评论有较高封号风险，使用者自行承担后果。

功能：
- 多关键词搜索高互动帖子
- 自动点赞
- AI 根据帖子内容生成口语化评论（融入宣发信息）
- 自动发送评论
- 随机间隔模拟人类行为
- 每日上限 + 熔断机制
- 混入纯互动评论降低广告感

用法:
    python scripts/auto_marketing.py \
        --keywords "AI创业,AI产品,人工智能应用" \
        --filter "AI,人工智能,创业,产品,应用,工具,效率" \
        --promo-info "aifunding是一个AI创业融资平台，帮助AI创业者对接投资人" \
        --max-notes 8 \
        --max-per-keyword 3 \
        --daily-limit 15 \
        --promo-ratio 0.7 \
        [--account ACCOUNT_NAME] \
        [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
from xhs.comment import post_comment as _raw_post_comment, _insert_text_and_enable, _wait_submit_enabled
from xhs.human import sleep_random
from xhs.like_favorite import like_feed_in_popup
from xhs.rate_limit import detect_rate_limit
from run_lock import RunLock

SCREENSHOT_DIR = Path.home() / ".xhs" / "marketing" / "screenshots"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("auto_marketing")

# ========== 状态文件 ==========
STATE_DIR = Path(os.path.expanduser("~/.xhs/marketing"))
STATE_FILE = STATE_DIR / "daily_state.json"
CIRCUIT_BREAKER_FILE = STATE_DIR / "circuit_breaker.json"


# ========== AI 评论生成 ==========

def generate_comment(title: str, content: str, author: str, promo_info: str, is_promo: bool) -> str:
    """用 AI 生成评论。优先用 Gemini，回退到模板。

    Args:
        title: 帖子标题
        content: 帖子内容片段
        author: 作者名
        promo_info: 宣发信息
        is_promo: 是否为推广评论（False则为纯互动评论）
    """
    try:
        from google import genai

        client = genai.Client()

        if is_promo:
            system_prompt = f"""你是一个小红书用户，正在浏览帖子并发表评论。
你的评论需要：
1. 口语化、自然，像真实用户在评论区的发言
2. 先对帖子内容做出真实回应（共鸣/提问/补充）
3. 然后自然地引出推广信息，不要生硬
4. 评论长度 30-80 字
5. 不要使用 emoji 过多（0-2个）
6. 不要使用 "推荐"、"安利" 等明显广告词
7. 语气要真诚，像是分享个人经验

推广信息：{promo_info}

重要：评论必须跟帖子内容相关，不能答非所问。推广信息要巧妙融入，不能太突兀。只输出评论文本本身，不要输出任何标题、解释、分析或格式说明。"""
        else:
            system_prompt = """你是一个小红书用户，正在浏览帖子并发表评论。
你的评论需要：
1. 口语化、自然，像真实用户在评论区的发言
2. 对帖子内容做出真实回应（共鸣/提问/补充/赞同）
3. 评论长度 15-50 字
4. emoji 0-2 个
5. 语气真诚自然

这是一条纯互动评论，不需要包含任何推广信息。
重要：只输出评论文本本身，不要输出任何标题、解释、分析或格式说明。"""

        user_prompt = f"帖子标题：{title}\n帖子内容：{content[:300]}\n作者：{author}\n\n请生成一条评论："

        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=user_prompt,
            config={'system_instruction': system_prompt, 'max_output_tokens': 150},
        )
        comment = response.text.strip()
        # 去除可能的引号包裹
        if comment.startswith('"') and comment.endswith('"'):
            comment = comment[1:-1]
        if comment.startswith("'") and comment.endswith("'"):
            comment = comment[1:-1]
        return comment

    except Exception as e:
        logger.warning("AI 生成评论失败: %s，使用模板", e)
        return _fallback_comment(title, is_promo, promo_info)


def _fallback_comment(title: str, is_promo: bool, promo_info: str) -> str:
    """AI 不可用时的模板评论。"""
    if is_promo:
        templates = [
            f"说得好！最近在用{promo_info.split('是')[0] if '是' in promo_info else 'aifunding'}，感觉不错",
            f"同感！之前朋友推荐过{promo_info.split('是')[0] if '是' in promo_info else 'aifunding'}，确实挺好用",
            f"写得很实用 有类似需求的可以看看{promo_info.split('是')[0] if '是' in promo_info else 'aifunding'}",
        ]
    else:
        templates = [
            "写得太好了 收藏了",
            "感谢分享！学到了",
            "说到心坎里了 真的",
            "这个总结很到位",
            "太实用了 马上试试",
            "正好需要这个 感谢楼主",
        ]
    return random.choice(templates)


# ========== 弹窗内评论（不跳转URL）==========

def _post_comment_in_popup(page: Page, content: str) -> dict:
    """在当前打开的详情弹窗中发表评论（不导航URL，反风控）。

    Returns:
        {"success": bool, "message": str}
    """
    try:
        # 查找评论输入触发区域
        engage_trigger = ".engage-bar .input-box .content-edit span"
        fallback_trigger = "div.input-box div.content-edit span"

        for _attempt in range(3):
            if page.has_element(engage_trigger) or page.has_element(fallback_trigger):
                break
            logger.info("等待评论输入框出现 (尝试 %d/3)...", _attempt + 1)
            sleep_random(2000, 4000)

        if page.has_element(engage_trigger):
            page.click_element(engage_trigger)
        elif page.has_element(fallback_trigger):
            page.click_element(fallback_trigger)
        else:
            return {"success": False, "message": "未找到评论输入框"}

        sleep_random(400, 800)

        # 输入评论
        input_sel = "#content-textarea"
        if not page.has_element(input_sel):
            input_sel = "div.content-edit"

        for _w in range(5):
            if page.has_element(input_sel):
                break
            sleep_random(500, 1000)

        _insert_text_and_enable(page, input_sel, content)
        sleep_random(600, 1200)

        # 等待提交按钮启用
        if not _wait_submit_enabled(page, timeout=5):
            # 重试触发 input
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
                return {"success": False, "message": "提交按钮未启用"}

        # 提交
        submit_sel = ".engage-bar button.submit"
        if not page.has_element(submit_sel):
            submit_sel = "div.bottom button.submit"
        page.click_element(submit_sel)
        sleep_random(2000, 3500)

        # 简单验证：检查是否出现我们评论内容的片段
        snippet = content[:15]
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
            return {"success": True, "message": "评论发送成功"}
        else:
            return {"success": True, "message": "评论已提交（未能验证是否显示）"}

    except Exception as e:
        return {"success": False, "message": str(e)}


def _screenshot_comment_proof(page: Page, feed_id: str, keyword: str) -> str | None:
    """评论成功后截图保存到 SCREENSHOT_DIR，返回文件路径或 None。"""
    try:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
        fname = f"{ts}_{feed_id[:12]}_{keyword}.jpg"
        path = SCREENSHOT_DIR / fname

        # 截取 noteContainer 区域（含评论）
        png = page.screenshot_element("#noteContainer")
        if png:
            path = path.with_suffix(".png")
            path.write_bytes(png)
        else:
            # 回退：全屏截图
            import base64 as _b64
            result = page._send_session(
                "Page.captureScreenshot", {"format": "jpeg", "quality": 85}
            )
            path.write_bytes(_b64.b64decode(result["data"]))

        logger.info("评论截图已保存: %s (%dKB)", path.name, path.stat().st_size // 1024)
        return str(path)
    except Exception as e:
        logger.warning("评论截图失败: %s", e)
        return None


# ========== URL信息提取 ==========

def _extract_feed_url_info(page: Page) -> dict:
    """从当前弹窗中提取 feed_id 和 xsec_token。"""
    result = page.evaluate('''
        (() => {
            const url = location.href;
            const match = url.match(/explore\\/([a-f0-9]+)/);
            const tokenMatch = url.match(/xsec_token=([^&]+)/);
            const container = document.querySelector("#noteContainer");
            const noteId = container?.getAttribute("data-note-id") || "";
            return {
                feed_id: match ? match[1] : noteId,
                xsec_token: tokenMatch ? tokenMatch[1] : "",
            };
        })()
    ''')
    return result or {}


# ========== 状态管理 ==========

def _load_daily_state() -> dict:
    """加载今日状态。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            if state.get("date") == today:
                return state
        except Exception:
            pass

    return {"date": today, "comments_sent": 0, "likes_done": 0, "errors": 0, "commented_feeds": []}


def _save_daily_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def _check_circuit_breaker() -> bool:
    """检查熔断器。如果触发了熔断，返回 True（应停止）。"""
    if not CIRCUIT_BREAKER_FILE.exists():
        return False
    try:
        cb = json.loads(CIRCUIT_BREAKER_FILE.read_text())
        until = datetime.fromisoformat(cb["until"])
        if datetime.now(timezone(timedelta(hours=8))) < until:
            logger.warning("熔断器已触发，暂停至 %s", cb["until"])
            return True
        # 熔断已过期，删除
        CIRCUIT_BREAKER_FILE.unlink()
    except Exception:
        pass
    return False


def _trigger_circuit_breaker(hours: int = 24) -> None:
    """触发熔断器。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    until = datetime.now(timezone(timedelta(hours=8))) + timedelta(hours=hours)
    cb = {"triggered_at": datetime.now(timezone(timedelta(hours=8))).isoformat(), "until": until.isoformat(), "reason": "连续失败触发熔断"}
    CIRCUIT_BREAKER_FILE.write_text(json.dumps(cb, ensure_ascii=False, indent=2))
    logger.warning("熔断器已触发，暂停 %d 小时", hours)


# ========== 时间窗口检查 ==========

def _in_active_hours() -> bool:
    """检查当前是否在活跃时间段（北京时间 8:00-20:00）。"""
    now_cst = datetime.now(timezone(timedelta(hours=8)))
    hour = now_cst.hour
    return 8 <= hour < 20


def _get_time_weight() -> float:
    """根据当前时间返回发送权重（高峰时段更活跃）。"""
    now_cst = datetime.now(timezone(timedelta(hours=8)))
    hour = now_cst.hour

    # 高峰时段
    if 8 <= hour < 9:      # 早高峰
        return 1.0
    elif 12 <= hour < 14:   # 午休
        return 1.0
    elif 19 <= hour < 21:   # 晚高峰
        return 1.0
    elif 9 <= hour < 12:    # 上午
        return 0.6
    elif 14 <= hour < 19:   # 下午
        return 0.5
    else:
        return 0.0  # 非活跃时段


# ========== 主流程 ==========

def run_marketing(
    page: Page,
    keywords: list[str],
    filter_terms: list[str],
    promo_info: str,
    max_notes: int = 8,
    max_per_keyword: int = 3,
    daily_limit: int = 15,
    promo_ratio: float = 0.7,
    dry_run: bool = False,
) -> dict:
    """执行一轮营销：搜索 → 点赞 → 评论。

    Args:
        page: CDP 页面
        keywords: 搜索关键词列表
        filter_terms: 相关性筛选词
        promo_info: 宣发信息
        max_notes: 本轮最多处理笔记数
        max_per_keyword: 每个关键词最多处理数
        daily_limit: 每日评论上限
        promo_ratio: 推广评论占比（0.7 = 70%推广, 30%纯互动）
        dry_run: 试运行模式（不实际发送评论）

    Returns:
        执行结果 JSON
    """
    # 检查熔断器
    if _check_circuit_breaker():
        return {"success": False, "reason": "circuit_breaker_active", "message": "熔断器已触发，暂停执行"}

    # 检查时间窗口（dry_run 模式跳过）
    if not _in_active_hours() and not dry_run:
        return {"success": False, "reason": "outside_active_hours", "message": "当前不在活跃时间段(北京时间 8:00-20:00)"}

    # 加载日状态
    state = _load_daily_state()
    remaining = daily_limit - state["comments_sent"]
    if remaining <= 0:
        return {"success": False, "reason": "daily_limit_reached", "message": f"今日已达评论上限({daily_limit})"}

    # 限制本轮处理数
    max_notes = min(max_notes, remaining)

    results = []
    total = 0
    consecutive_errors = 0
    seen_titles = set()
    seen_feeds = set(state.get("commented_feeds", []))

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

        sleep_random(2000, 3500)

        # 检查是否被风控/限流
        rate_limit = detect_rate_limit(page)
        if rate_limit:
            logger.error("检测到风控: %s", rate_limit["message"])
            return {
                "success": False,
                "reason": "rate_limited",
                "rate_limited": True,
                "rate_limit_type": rate_limit["type"],
                "message": rate_limit["message"],
                "instruction": "请通过 --proxy 参数配置代理后重试",
                "total_processed": total,
                "results": results,
            }

        cards = _analyze_feed_cards(page)
        logger.info("找到 %d 张卡片", len(cards))

        if filter_terms:
            relevant, _ = _filter_relevant_cards(cards, filter_terms)
        else:
            relevant = cards

        logger.info("相关卡片: %d", len(relevant))

        processed_this_kw = 0
        for card in relevant:
            if total >= max_notes or processed_this_kw >= max_per_keyword:
                break

            title = card.get("title", "")
            if title and title in seen_titles:
                continue
            if title:
                seen_titles.add(title)

            logger.info("打开: %s", title[:60] or "(无标题)")

            try:
                if not _click_card_by_position(page, card):
                    logger.warning("点击卡片失败，跳过")
                    continue

                sleep_random(2500, 4000)

                # 等待 #noteContainer 加载
                container_loaded = False
                for _wait in range(10):
                    if page.has_element('#noteContainer'):
                        container_loaded = True
                        break
                    time.sleep(1)
                if not container_loaded:
                    logger.warning("noteContainer 未加载，跳过此卡片")
                    _close_detail(page)
                    sleep_random(1000, 2000)
                    continue

                # 提取详情
                detail = _extract_detail_info(page) or {}
                url_info = _extract_feed_url_info(page)
                feed_id = url_info.get("feed_id", "")

                # 跳过已评论过的帖子
                if feed_id and feed_id in seen_feeds:
                    logger.info("已评论过此帖，跳过: %s", feed_id)
                    _close_detail(page)
                    sleep_random(1000, 2000)
                    continue

                post_title = detail.get("title", title)[:80]
                post_content = detail.get("content", "")[:300]
                post_author = detail.get("author", "")

                # 1. 点赞（dry_run 模式也点赞，因为点赞风险低）
                like_result = like_feed_in_popup(page)
                logger.info("点赞: %s", like_result.message)

                # 2. 模拟阅读
                sleep_random(3000, 6000)

                # 3. 生成评论
                is_promo = random.random() < promo_ratio
                comment_text = generate_comment(
                    post_title, post_content, post_author, promo_info, is_promo
                )
                logger.info("生成评论 [%s]: %s", "推广" if is_promo else "互动", comment_text)

                # 4. 发送评论
                comment_result = {"success": False, "message": "dry_run"}
                if not dry_run:
                    comment_result = _post_comment_in_popup(page, comment_text)
                    logger.info("评论结果: %s", comment_result["message"])

                    if comment_result["success"]:
                        consecutive_errors = 0
                        state["comments_sent"] += 1
                        if feed_id:
                            state["commented_feeds"].append(feed_id)
                            seen_feeds.add(feed_id)
                        _save_daily_state(state)

                        # 截图留证
                        screenshot_path = _screenshot_comment_proof(page, feed_id or "unknown", kw)
                        comment_result["screenshot"] = screenshot_path
                    else:
                        consecutive_errors += 1
                        state["errors"] += 1
                        _save_daily_state(state)

                        # 熔断检查：连续3次失败触发
                        if consecutive_errors >= 3:
                            _trigger_circuit_breaker(24)
                            results.append({
                                "index": total + 1,
                                "keyword": kw,
                                "title": post_title,
                                "author": post_author,
                                "comment": comment_text,
                                "comment_type": "promo" if is_promo else "organic",
                                "like_success": like_result.success,
                                "comment_success": False,
                                "comment_message": "熔断器触发",
                                "feed_id": feed_id,
                            })
                            _close_detail(page)
                            return {
                                "success": False,
                                "reason": "circuit_breaker_triggered",
                                "message": "连续失败3次，触发熔断，暂停24小时",
                                "total_processed": total + 1,
                                "comments_sent_today": state["comments_sent"],
                                "results": results,
                            }
                else:
                    comment_result = {"success": True, "message": "dry_run - 未实际发送"}

                results.append({
                    "index": total + 1,
                    "keyword": kw,
                    "title": post_title,
                    "author": post_author,
                    "likes": detail.get("likes", ""),
                    "comment": comment_text,
                    "comment_type": "promo" if is_promo else "organic",
                    "like_success": like_result.success,
                    "comment_success": comment_result["success"],
                    "comment_message": comment_result["message"],
                    "feed_id": feed_id,
                    "screenshot": comment_result.get("screenshot"),
                })

                total += 1
                processed_this_kw += 1

                _close_detail(page)

                # 随机间隔 3-8 分钟（评论间隔要长）
                if total < max_notes:
                    wait_min = random.randint(180, 480)
                    logger.info("等待 %d 秒后处理下一条...", wait_min)
                    time.sleep(wait_min)

            except Exception as e:
                logger.warning("处理卡片异常: %s", e)
                consecutive_errors += 1
                state["errors"] += 1
                _save_daily_state(state)
                try:
                    _close_detail(page)
                except Exception:
                    pass
                sleep_random(2000, 4000)

                if consecutive_errors >= 3:
                    _trigger_circuit_breaker(24)
                    return {
                        "success": False,
                        "reason": "circuit_breaker_triggered",
                        "message": "连续异常3次，触发熔断",
                        "total_processed": total,
                        "results": results,
                    }
                continue

        # 关键词间间隔
        if ki < len(keywords) - 1 and total < max_notes:
            sleep_random(3000, 6000)

    return {
        "success": True,
        "total_processed": total,
        "comments_sent_today": state["comments_sent"],
        "daily_limit": daily_limit,
        "dry_run": dry_run,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="小红书自动营销")
    parser.add_argument("--keywords", required=True, help="搜索关键词，逗号分隔")
    parser.add_argument("--filter", default="", help="相关性筛选词，逗号分隔")
    parser.add_argument("--promo-info", required=True, help="宣发信息描述")
    parser.add_argument("--max-notes", type=int, default=8, help="本轮最多处理笔记数")
    parser.add_argument("--max-per-keyword", type=int, default=3, help="每个关键词最多处理数")
    parser.add_argument("--daily-limit", type=int, default=15, help="每日评论上限")
    parser.add_argument("--promo-ratio", type=float, default=0.7, help="推广评论占比")
    parser.add_argument("--account", default="", help="账号名称")
    parser.add_argument("--proxy", default="", help="代理地址 (socks5://host:port 或 http://user:pass@host:port)")
    parser.add_argument("--dry-run", action="store_true", help="试运行模式")
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    filter_terms = [f.strip() for f in args.filter.split(",") if f.strip()]

    # 设置代理（供 chrome_launcher 使用）
    if args.proxy:
        os.environ["XHS_PROXY"] = args.proxy
        logger.info("代理已设置: %s", args.proxy.split("@")[-1] if "@" in args.proxy else args.proxy)

    if not _in_active_hours() and not args.dry_run:
        # dry-run 模式不受时间限制
        print(json.dumps({
            "success": False,
            "reason": "outside_active_hours",
            "message": "当前不在活跃时间段(北京时间 8:00-20:00)",
        }, ensure_ascii=False, indent=2))
        return

    lock = RunLock()
    if not lock.acquire(timeout=60):
        print(json.dumps({
            "success": False,
            "reason": "lock_failed",
            "message": "无法获取锁，另一个操作正在进行",
        }, ensure_ascii=False, indent=2))
        return

    try:
        browser = Browser()
        page = browser.get_or_create_page()

        result = run_marketing(
            page=page,
            keywords=keywords,
            filter_terms=filter_terms,
            promo_info=args.promo_info,
            max_notes=args.max_notes,
            max_per_keyword=args.max_per_keyword,
            daily_limit=args.daily_limit,
            promo_ratio=args.promo_ratio,
            dry_run=args.dry_run,
        )

        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        lock.release()
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
