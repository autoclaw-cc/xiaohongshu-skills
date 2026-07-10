"""媒体下载：把小红书视频/图片直链保存到本地。

小红书 CDN 直链带时效签名，需在抓取当下下载；请求需带浏览器 UA 与 referer，
且禁用本机 SOCKS/HTTP 代理（直连 CDN，避免 python-socks 报错）。
"""

from __future__ import annotations

import os
from pathlib import Path

import requests

from .errors import MediaDownloadError

# 直连 CDN，屏蔽环境里的代理配置
_NO_PROXIES: dict[str, str] = {"http": "", "https": ""}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.xiaohongshu.com/",
}

_CHUNK = 1 << 16  # 64 KiB


def download_file(url: str, dest: str | Path, *, timeout: float = 60.0) -> Path:
    """流式下载 url 到 dest（绝对路径），返回落地路径。

    先写入 .part 临时文件，成功后原子重命名，避免半截文件。
    """
    if not url:
        raise MediaDownloadError("直链为空")
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_path.with_suffix(dest_path.suffix + ".part")

    try:
        with requests.get(
            url,
            headers=_HEADERS,
            stream=True,
            timeout=timeout,
            proxies=_NO_PROXIES,
        ) as resp:
            resp.raise_for_status()
            written = 0
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=_CHUNK):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
            if written == 0:
                raise MediaDownloadError("下载内容为空（0 字节）")
    except requests.RequestException as e:
        tmp.unlink(missing_ok=True)
        raise MediaDownloadError(f"{type(e).__name__}: {e}") from e

    os.replace(tmp, dest_path)
    return dest_path


def download_video(url: str, dest_dir: str | Path, feed_id: str, *, timeout: float = 120.0) -> Path:
    """下载视频到 dest_dir/<feed_id>.mp4，返回落地路径。"""
    dest = Path(dest_dir) / f"{feed_id}.mp4"
    return download_file(url, dest, timeout=timeout)
