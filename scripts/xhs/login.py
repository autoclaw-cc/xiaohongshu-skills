"""登录管理，对应 Go xiaohongshu/login.go。"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
import time

from .cdp import Page
from .selectors import LOGIN_STATUS, QRCODE_IMG
from .urls import EXPLORE_URL

logger = logging.getLogger(__name__)


def check_login_status(page: Page) -> bool:
    """检查登录状态。

    Returns:
        True 已登录，False 未登录。
    """
    page.navigate(EXPLORE_URL)
    page.wait_for_load()
    time.sleep(1)

    return page.has_element(LOGIN_STATUS)


def fetch_qrcode(page: Page) -> tuple[str, bool]:
    """获取登录二维码。

    Returns:
        (qrcode_src, already_logged_in)
        - 如果已登录，返回 ("", True)
        - 如果未登录，返回 (qrcode_base64_or_url, False)
    """
    page.navigate(EXPLORE_URL)
    page.wait_for_load()
    time.sleep(2)

    # 检查是否已登录
    if page.has_element(LOGIN_STATUS):
        return "", True

    # 获取二维码图片 src
    src = page.get_element_attribute(QRCODE_IMG, "src")
    if not src:
        raise RuntimeError("二维码图片 src 为空")

    return src, False


def save_qrcode_to_file(src: str) -> str:
    """将二维码 data URL 保存为临时 PNG 文件。

    Args:
        src: 二维码图片的 data URL（data:image/png;base64,...）或普通 URL。

    Returns:
        保存的文件绝对路径。
    """
    prefix = "data:image/png;base64,"
    if src.startswith(prefix):
        img_data = base64.b64decode(src[len(prefix) :])
    elif src.startswith("data:image/"):
        # 处理其他 MIME 类型，如 data:image/jpeg;base64,...
        _, encoded = src.split(",", 1)
        img_data = base64.b64decode(encoded)
    else:
        # 不是 data URL，无法保存
        raise ValueError(f"不支持的二维码格式，需要 data URL: {src[:50]}...")

    qr_dir = os.path.join(tempfile.gettempdir(), "xhs")
    os.makedirs(qr_dir, exist_ok=True)
    filepath = os.path.join(qr_dir, "login_qrcode.png")

    with open(filepath, "wb") as f:
        f.write(img_data)

    logger.info("二维码已保存: %s", filepath)
    return filepath


def wait_for_login(page: Page, timeout: float = 120.0) -> bool:
    """等待扫码登录完成。

    Args:
        page: CDP 页面对象。
        timeout: 超时时间（秒）。

    Returns:
        True 登录成功，False 超时。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if page.has_element(LOGIN_STATUS):
            logger.info("登录成功")
            return True
        time.sleep(0.5)
    return False
