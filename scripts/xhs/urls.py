"""小红书 URL 常量和构建函数。"""

import os
from urllib.parse import urlencode

# 默认使用 rednote.com（小红书国际版）。可通过 XHS_BASE_DOMAIN 环境变量切回
# 国内域名，例如 XHS_BASE_DOMAIN=xiaohongshu.com。
BASE_DOMAIN = os.environ.get("XHS_BASE_DOMAIN", "rednote.com")

# 基础页面
EXPLORE_URL = f"https://www.{BASE_DOMAIN}/explore"
HOME_URL = f"https://www.{BASE_DOMAIN}"
PUBLISH_URL = f"https://creator.{BASE_DOMAIN}/publish/publish?source=official"


def make_feed_detail_url(feed_id: str, xsec_token: str) -> str:
    """构建 feed 详情页 URL。"""
    return (
        f"https://www.{BASE_DOMAIN}/explore/{feed_id}?xsec_token={xsec_token}&xsec_source=pc_feed"
    )


def make_search_url(keyword: str) -> str:
    """构建搜索结果页 URL。"""
    params = urlencode({"keyword": keyword, "source": "web_explore_feed"})
    return f"https://www.{BASE_DOMAIN}/search_result?{params}"


def make_user_profile_url(user_id: str, xsec_token: str) -> str:
    """构建用户主页 URL。"""
    return (
        f"https://www.{BASE_DOMAIN}/user/profile/{user_id}"
        f"?xsec_token={xsec_token}&xsec_source=pc_note"
    )
