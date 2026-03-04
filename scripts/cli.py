"""统一 CLI 入口，对应 Go MCP 工具的 13 个子命令。

全局选项: --host, --port, --account
输出: JSON（ensure_ascii=False）
退出码: 0=成功, 1=未登录, 2=错误
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("xhs-cli")


def _output(data: dict, exit_code: int = 0) -> None:
    """输出 JSON 并退出。"""
    print(json.dumps(data, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def _connect(args: argparse.Namespace):
    """连接到 Chrome 并返回 (browser, page)。"""
    from xhs.cdp import Browser

    browser = Browser(host=args.host, port=args.port)
    browser.connect()
    page = browser.new_page()
    return browser, page


def _headless_fallback(port: int) -> None:
    """Headless 模式未登录时自动降级到有窗口模式。"""
    from chrome_launcher import restart_chrome

    logger.info("Headless 模式未登录，切换到有窗口模式...")
    restart_chrome(port=port, headless=False)
    _output(
        {
            "success": False,
            "error": "未登录",
            "action": "switched_to_headed",
            "message": "已切换到有窗口模式，请在浏览器中扫码登录",
        },
        exit_code=1,
    )


# ========== 子命令实现 ==========


def cmd_check_login(args: argparse.Namespace) -> None:
    """检查登录状态。"""
    from xhs.login import check_login_status

    browser, page = _connect(args)
    try:
        logged_in = check_login_status(page)
        _output({"logged_in": logged_in}, exit_code=0 if logged_in else 1)
    finally:
        browser.close_page(page)
        browser.close()


def cmd_login(args: argparse.Namespace) -> None:
    """获取登录二维码并等待扫码。"""
    from xhs.login import fetch_qrcode, save_qrcode_to_file, wait_for_login

    browser, page = _connect(args)
    try:
        src, already = fetch_qrcode(page)
        if already:
            _output({"logged_in": True, "message": "已登录"})
        else:
            # 保存二维码到临时文件
            qrcode_path = save_qrcode_to_file(src)
            print(
                json.dumps(
                    {
                        "qrcode_path": qrcode_path,
                        "message": "请扫码登录，二维码已保存到文件",
                    },
                    ensure_ascii=False,
                )
            )
            success = wait_for_login(page, timeout=120)
            _output(
                {"logged_in": success, "message": "登录成功" if success else "登录超时"},
                exit_code=0 if success else 2,
            )
    finally:
        browser.close_page(page)
        browser.close()


def cmd_delete_cookies(args: argparse.Namespace) -> None:
    """删除 cookies。"""
    from xhs.cookies import delete_cookies, get_cookies_file_path

    path = get_cookies_file_path(args.account)
    delete_cookies(path)
    _output({"success": True, "message": f"已删除 cookies: {path}"})


def cmd_list_feeds(args: argparse.Namespace) -> None:
    """获取首页 Feed 列表。"""
    from xhs.feeds import list_feeds

    browser, page = _connect(args)
    try:
        feeds = list_feeds(page)
        _output({"feeds": [f.to_dict() for f in feeds], "count": len(feeds)})
    finally:
        browser.close_page(page)
        browser.close()


def cmd_search_feeds(args: argparse.Namespace) -> None:
    """搜索 Feeds。"""
    from xhs.search import search_feeds
    from xhs.types import FilterOption

    filter_opt = FilterOption(
        sort_by=args.sort_by or "",
        note_type=args.note_type or "",
        publish_time=args.publish_time or "",
        search_scope=args.search_scope or "",
        location=args.location or "",
    )

    browser, page = _connect(args)
    try:
        feeds = search_feeds(page, args.keyword, filter_opt)
        _output({"feeds": [f.to_dict() for f in feeds], "count": len(feeds)})
    finally:
        browser.close_page(page)
        browser.close()


def cmd_get_feed_detail(args: argparse.Namespace) -> None:
    """获取 Feed 详情。"""
    from xhs.feed_detail import get_feed_detail
    from xhs.types import CommentLoadConfig

    config = CommentLoadConfig(
        click_more_replies=args.click_more_replies,
        max_replies_threshold=args.max_replies_threshold,
        max_comment_items=args.max_comment_items,
        scroll_speed=args.scroll_speed,
    )

    browser, page = _connect(args)
    try:
        detail = get_feed_detail(
            page,
            args.feed_id,
            args.xsec_token,
            load_all_comments=args.load_all_comments,
            config=config,
        )
        _output(detail.to_dict())
    finally:
        browser.close_page(page)
        browser.close()


def cmd_user_profile(args: argparse.Namespace) -> None:
    """获取用户主页。"""
    from xhs.user_profile import get_user_profile

    browser, page = _connect(args)
    try:
        profile = get_user_profile(page, args.user_id, args.xsec_token)
        _output(profile.to_dict())
    finally:
        browser.close_page(page)
        browser.close()


def cmd_post_comment(args: argparse.Namespace) -> None:
    """发表评论。"""
    from xhs.comment import post_comment

    browser, page = _connect(args)
    try:
        post_comment(page, args.feed_id, args.xsec_token, args.content)
        _output({"success": True, "message": "评论发送成功"})
    finally:
        browser.close_page(page)
        browser.close()


def cmd_reply_comment(args: argparse.Namespace) -> None:
    """回复评论。"""
    from xhs.comment import reply_comment

    browser, page = _connect(args)
    try:
        reply_comment(
            page,
            args.feed_id,
            args.xsec_token,
            args.content,
            comment_id=args.comment_id or "",
            user_id=args.user_id or "",
        )
        _output({"success": True, "message": "回复成功"})
    finally:
        browser.close_page(page)
        browser.close()


def cmd_like_feed(args: argparse.Namespace) -> None:
    """点赞/取消点赞。"""
    from xhs.like_favorite import like_feed, unlike_feed

    browser, page = _connect(args)
    try:
        if args.unlike:
            result = unlike_feed(page, args.feed_id, args.xsec_token)
        else:
            result = like_feed(page, args.feed_id, args.xsec_token)
        _output(result.to_dict())
    finally:
        browser.close_page(page)
        browser.close()


def cmd_favorite_feed(args: argparse.Namespace) -> None:
    """收藏/取消收藏。"""
    from xhs.like_favorite import favorite_feed, unfavorite_feed

    browser, page = _connect(args)
    try:
        if args.unfavorite:
            result = unfavorite_feed(page, args.feed_id, args.xsec_token)
        else:
            result = favorite_feed(page, args.feed_id, args.xsec_token)
        _output(result.to_dict())
    finally:
        browser.close_page(page)
        browser.close()


def cmd_publish(args: argparse.Namespace) -> None:
    """发布图文内容。"""
    from image_downloader import process_images
    from xhs.login import check_login_status
    from xhs.publish import publish_image_content
    from xhs.types import PublishImageContent

    # 读取标题和正文
    with open(args.title_file, encoding="utf-8") as f:
        title = f.read().strip()
    with open(args.content_file, encoding="utf-8") as f:
        content = f.read().strip()

    # 处理图片
    image_paths = process_images(args.images) if args.images else []
    if not image_paths:
        _output({"success": False, "error": "没有有效的图片"}, exit_code=2)

    browser, page = _connect(args)
    try:
        # headless 模式登录检查 + 自动降级
        headless = getattr(args, "headless", False)
        if headless and not check_login_status(page):
            browser.close_page(page)
            browser.close()
            _headless_fallback(args.port)
            return

        publish_image_content(
            page,
            PublishImageContent(
                title=title,
                content=content,
                tags=args.tags or [],
                image_paths=image_paths,
                schedule_time=args.schedule_at,
                is_original=args.original,
                visibility=args.visibility or "",
            ),
        )
        _output({"success": True, "title": title, "images": len(image_paths), "status": "发布完成"})
    finally:
        browser.close_page(page)
        browser.close()


def cmd_fill_publish(args: argparse.Namespace) -> None:
    """只填写图文表单，不发布。"""
    from image_downloader import process_images
    from xhs.publish import fill_publish_form
    from xhs.types import PublishImageContent

    with open(args.title_file, encoding="utf-8") as f:
        title = f.read().strip()
    with open(args.content_file, encoding="utf-8") as f:
        content = f.read().strip()

    image_paths = process_images(args.images) if args.images else []
    if not image_paths:
        _output({"success": False, "error": "没有有效的图片"}, exit_code=2)

    browser, page = _connect(args)
    try:
        fill_publish_form(
            page,
            PublishImageContent(
                title=title,
                content=content,
                tags=args.tags or [],
                image_paths=image_paths,
                schedule_time=args.schedule_at,
                is_original=args.original,
                visibility=args.visibility or "",
            ),
        )
        _output(
            {
                "success": True,
                "title": title,
                "images": len(image_paths),
                "status": "表单已填写，等待确认发布",
            }
        )
    finally:
        browser.close_page(page)
        browser.close()


def cmd_fill_publish_video(args: argparse.Namespace) -> None:
    """只填写视频表单，不发布。"""
    from xhs.publish_video import fill_publish_video_form
    from xhs.types import PublishVideoContent

    with open(args.title_file, encoding="utf-8") as f:
        title = f.read().strip()
    with open(args.content_file, encoding="utf-8") as f:
        content = f.read().strip()

    browser, page = _connect(args)
    try:
        fill_publish_video_form(
            page,
            PublishVideoContent(
                title=title,
                content=content,
                tags=args.tags or [],
                video_path=args.video,
                schedule_time=args.schedule_at,
                visibility=args.visibility or "",
            ),
        )
        _output(
            {
                "success": True,
                "title": title,
                "video": args.video,
                "status": "视频表单已填写，等待确认发布",
            }
        )
    finally:
        browser.close_page(page)
        browser.close()


def cmd_click_publish(args: argparse.Namespace) -> None:
    """点击发布按钮（在用户确认后调用）。"""
    from xhs.publish import click_publish_button

    browser, page = _connect(args)
    try:
        click_publish_button(page)
        _output({"success": True, "status": "发布完成"})
    finally:
        browser.close_page(page)
        browser.close()


def cmd_long_article(args: argparse.Namespace) -> None:
    """长文模式：填写内容 + 一键排版，返回模板列表。"""
    from xhs.publish_long_article import publish_long_article

    with open(args.title_file, encoding="utf-8") as f:
        title = f.read().strip()
    with open(args.content_file, encoding="utf-8") as f:
        content = f.read().strip()

    browser, page = _connect(args)
    try:
        template_names = publish_long_article(
            page,
            title=title,
            content=content,
            image_paths=args.images,
        )
        _output(
            {
                "success": True,
                "templates": template_names,
                "status": "长文已填写，请选择模板",
            }
        )
    finally:
        browser.close_page(page)
        browser.close()


def cmd_select_template(args: argparse.Namespace) -> None:
    """选择排版模板。"""
    from xhs.publish_long_article import select_template

    browser, page = _connect(args)
    try:
        selected = select_template(page, args.name)
        if selected:
            _output({"success": True, "template": args.name, "status": "模板已选择"})
        else:
            _output(
                {"success": False, "error": f"未找到模板: {args.name}"},
                exit_code=2,
            )
    finally:
        browser.close_page(page)
        browser.close()


def cmd_next_step(args: argparse.Namespace) -> None:
    """点击下一步 + 填写发布页描述。"""
    from xhs.publish_long_article import click_next_and_fill_description

    with open(args.content_file, encoding="utf-8") as f:
        description = f.read().strip()

    browser, page = _connect(args)
    try:
        click_next_and_fill_description(page, description)
        _output({"success": True, "status": "已进入发布页，等待确认发布"})
    finally:
        browser.close_page(page)
        browser.close()


def cmd_publish_video(args: argparse.Namespace) -> None:
    """发布视频内容。"""
    from xhs.login import check_login_status
    from xhs.publish_video import publish_video_content
    from xhs.types import PublishVideoContent

    with open(args.title_file, encoding="utf-8") as f:
        title = f.read().strip()
    with open(args.content_file, encoding="utf-8") as f:
        content = f.read().strip()

    browser, page = _connect(args)
    try:
        # headless 模式登录检查 + 自动降级
        headless = getattr(args, "headless", False)
        if headless and not check_login_status(page):
            browser.close_page(page)
            browser.close()
            _headless_fallback(args.port)
            return

        publish_video_content(
            page,
            PublishVideoContent(
                title=title,
                content=content,
                tags=args.tags or [],
                video_path=args.video,
                schedule_time=args.schedule_at,
                visibility=args.visibility or "",
            ),
        )
        _output({"success": True, "title": title, "video": args.video, "status": "发布完成"})
    finally:
        browser.close_page(page)
        browser.close()


# ========== 参数解析 ==========


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="xhs-cli",
        description="小红书自动化 CLI",
    )

    # 全局选项
    parser.add_argument("--host", default="127.0.0.1", help="Chrome 调试主机 (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9222, help="Chrome 调试端口 (default: 9222)")
    parser.add_argument("--account", default="", help="账号名称")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # check-login
    sub = subparsers.add_parser("check-login", help="检查登录状态")
    sub.set_defaults(func=cmd_check_login)

    # login
    sub = subparsers.add_parser("login", help="登录（扫码）")
    sub.set_defaults(func=cmd_login)

    # delete-cookies
    sub = subparsers.add_parser("delete-cookies", help="删除 cookies")
    sub.set_defaults(func=cmd_delete_cookies)

    # list-feeds
    sub = subparsers.add_parser("list-feeds", help="获取首页 Feed 列表")
    sub.set_defaults(func=cmd_list_feeds)

    # search-feeds
    sub = subparsers.add_parser("search-feeds", help="搜索 Feeds")
    sub.add_argument("--keyword", required=True, help="搜索关键词")
    sub.add_argument("--sort-by", help="排序: 综合|最新|最多点赞|最多评论|最多收藏")
    sub.add_argument("--note-type", help="类型: 不限|视频|图文")
    sub.add_argument("--publish-time", help="时间: 不限|一天内|一周内|半年内")
    sub.add_argument("--search-scope", help="范围: 不限|已看过|未看过|已关注")
    sub.add_argument("--location", help="位置: 不限|同城|附近")
    sub.set_defaults(func=cmd_search_feeds)

    # get-feed-detail
    sub = subparsers.add_parser("get-feed-detail", help="获取 Feed 详情")
    sub.add_argument("--feed-id", required=True, help="Feed ID")
    sub.add_argument("--xsec-token", required=True, help="xsec_token")
    sub.add_argument("--load-all-comments", action="store_true", help="加载全部评论")
    sub.add_argument("--click-more-replies", action="store_true", help="点击展开更多回复")
    sub.add_argument("--max-replies-threshold", type=int, default=10, help="展开回复数阈值")
    sub.add_argument("--max-comment-items", type=int, default=0, help="最大评论数 (0=不限)")
    sub.add_argument("--scroll-speed", default="normal", help="滚动速度: slow|normal|fast")
    sub.set_defaults(func=cmd_get_feed_detail)

    # user-profile
    sub = subparsers.add_parser("user-profile", help="获取用户主页")
    sub.add_argument("--user-id", required=True, help="用户 ID")
    sub.add_argument("--xsec-token", required=True, help="xsec_token")
    sub.set_defaults(func=cmd_user_profile)

    # post-comment
    sub = subparsers.add_parser("post-comment", help="发表评论")
    sub.add_argument("--feed-id", required=True, help="Feed ID")
    sub.add_argument("--xsec-token", required=True, help="xsec_token")
    sub.add_argument("--content", required=True, help="评论内容")
    sub.set_defaults(func=cmd_post_comment)

    # reply-comment
    sub = subparsers.add_parser("reply-comment", help="回复评论")
    sub.add_argument("--feed-id", required=True, help="Feed ID")
    sub.add_argument("--xsec-token", required=True, help="xsec_token")
    sub.add_argument("--content", required=True, help="回复内容")
    sub.add_argument("--comment-id", help="目标评论 ID")
    sub.add_argument("--user-id", help="目标用户 ID")
    sub.set_defaults(func=cmd_reply_comment)

    # like-feed
    sub = subparsers.add_parser("like-feed", help="点赞")
    sub.add_argument("--feed-id", required=True, help="Feed ID")
    sub.add_argument("--xsec-token", required=True, help="xsec_token")
    sub.add_argument("--unlike", action="store_true", help="取消点赞")
    sub.set_defaults(func=cmd_like_feed)

    # favorite-feed
    sub = subparsers.add_parser("favorite-feed", help="收藏")
    sub.add_argument("--feed-id", required=True, help="Feed ID")
    sub.add_argument("--xsec-token", required=True, help="xsec_token")
    sub.add_argument("--unfavorite", action="store_true", help="取消收藏")
    sub.set_defaults(func=cmd_favorite_feed)

    # publish
    sub = subparsers.add_parser("publish", help="发布图文")
    sub.add_argument("--title-file", required=True, help="标题文件路径")
    sub.add_argument("--content-file", required=True, help="正文文件路径")
    sub.add_argument("--images", nargs="+", required=True, help="图片路径/URL")
    sub.add_argument("--tags", nargs="*", help="标签")
    sub.add_argument("--schedule-at", help="定时发布 (ISO8601)")
    sub.add_argument("--original", action="store_true", help="声明原创")
    sub.add_argument("--visibility", help="可见范围")
    sub.add_argument("--headless", action="store_true", help="无头模式（未登录自动降级）")
    sub.set_defaults(func=cmd_publish)

    # publish-video
    sub = subparsers.add_parser("publish-video", help="发布视频")
    sub.add_argument("--title-file", required=True, help="标题文件路径")
    sub.add_argument("--content-file", required=True, help="正文文件路径")
    sub.add_argument("--video", required=True, help="视频文件路径")
    sub.add_argument("--tags", nargs="*", help="标签")
    sub.add_argument("--schedule-at", help="定时发布 (ISO8601)")
    sub.add_argument("--visibility", help="可见范围")
    sub.add_argument("--headless", action="store_true", help="无头模式（未登录自动降级）")
    sub.set_defaults(func=cmd_publish_video)

    # fill-publish（只填写图文表单，不发布）
    sub = subparsers.add_parser("fill-publish", help="填写图文表单（不发布）")
    sub.add_argument("--title-file", required=True, help="标题文件路径")
    sub.add_argument("--content-file", required=True, help="正文文件路径")
    sub.add_argument("--images", nargs="+", required=True, help="图片路径/URL")
    sub.add_argument("--tags", nargs="*", help="标签")
    sub.add_argument("--schedule-at", help="定时发布 (ISO8601)")
    sub.add_argument("--original", action="store_true", help="声明原创")
    sub.add_argument("--visibility", help="可见范围")
    sub.set_defaults(func=cmd_fill_publish)

    # fill-publish-video（只填写视频表单，不发布）
    sub = subparsers.add_parser("fill-publish-video", help="填写视频表单（不发布）")
    sub.add_argument("--title-file", required=True, help="标题文件路径")
    sub.add_argument("--content-file", required=True, help="正文文件路径")
    sub.add_argument("--video", required=True, help="视频文件路径")
    sub.add_argument("--tags", nargs="*", help="标签")
    sub.add_argument("--schedule-at", help="定时发布 (ISO8601)")
    sub.add_argument("--visibility", help="可见范围")
    sub.set_defaults(func=cmd_fill_publish_video)

    # click-publish（点击发布按钮）
    sub = subparsers.add_parser("click-publish", help="点击发布按钮")
    sub.set_defaults(func=cmd_click_publish)

    # long-article（长文模式）
    sub = subparsers.add_parser("long-article", help="长文模式：填写 + 一键排版")
    sub.add_argument("--title-file", required=True, help="标题文件路径")
    sub.add_argument("--content-file", required=True, help="正文文件路径")
    sub.add_argument("--images", nargs="*", help="可选图片路径")
    sub.set_defaults(func=cmd_long_article)

    # select-template（选择模板）
    sub = subparsers.add_parser("select-template", help="选择排版模板")
    sub.add_argument("--name", required=True, help="模板名称")
    sub.set_defaults(func=cmd_select_template)

    # next-step（下一步 + 填写描述）
    sub = subparsers.add_parser("next-step", help="点击下一步 + 填写描述")
    sub.add_argument("--content-file", required=True, help="描述内容文件路径")
    sub.set_defaults(func=cmd_next_step)

    return parser


def main() -> None:
    """CLI 入口。"""
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
    except Exception as e:
        logger.error("执行失败: %s", e, exc_info=True)
        _output({"success": False, "error": str(e)}, exit_code=2)


if __name__ == "__main__":
    main()
