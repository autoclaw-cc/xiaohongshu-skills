"""小红书 URL 常量和构建函数。"""

import os
from urllib.parse import urlencode

# XHS_BASE_DOMAIN 支持 rednote.com（国际版）和 xiaohongshu.com（中国版）
# 默认 xiaohongshu.com；国际用户设置 XHS_BASE_DOMAIN=rednote.com
_BASE = os.environ.get("XHS_BASE_DOMAIN", "xiaohongshu.com")

# 基础页面
EXPLORE_URL = f"https://www.{_BASE}/explore"
HOME_URL = f"https://www.{_BASE}"
PUBLISH_URL = f"https://creator.{_BASE}/publish/publish?source=official"


def make_feed_detail_url(feed_id: str, xsec_token: str) -> str:
    """构建 feed 详情页 URL。"""
    return (
        f"https://www.{_BASE}/explore/{feed_id}"
        f"?xsec_token={xsec_token}&xsec_source=pc_feed"
    )


def make_search_url(keyword: str) -> str:
    """构建搜索结果页 URL。"""
    params = urlencode({"keyword": keyword, "source": "web_explore_feed"})
    return f"https://www.{_BASE}/search_result?{params}"


def make_user_profile_url(user_id: str, xsec_token: str) -> str:
    """构建用户主页 URL。"""
    return (
        f"https://www.{_BASE}/user/profile/{user_id}"
        f"?xsec_token={xsec_token}&xsec_source=pc_note"
    )
