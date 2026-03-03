"""Chrome 进程管理（跨平台），对应 Go browser/browser.go 的进程管理部分。"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import signal
import subprocess
import time

from xhs.stealth import STEALTH_ARGS

logger = logging.getLogger(__name__)

# 默认远程调试端口
DEFAULT_PORT = 9222

# 各平台 Chrome 默认路径
_CHROME_PATHS: dict[str, list[str]] = {
    "Darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ],
    "Linux": [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
    ],
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
}


def find_chrome() -> str | None:
    """查找 Chrome 可执行文件路径。"""
    # 环境变量优先
    env_path = os.getenv("CHROME_BIN")
    if env_path and os.path.isfile(env_path):
        return env_path

    # which/where 查找
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if chrome:
        return chrome

    # 平台默认路径
    system = platform.system()
    for path in _CHROME_PATHS.get(system, []):
        if os.path.isfile(path):
            return path

    return None


def launch_chrome(
    port: int = DEFAULT_PORT,
    headless: bool = False,
    user_data_dir: str | None = None,
    chrome_bin: str | None = None,
) -> subprocess.Popen:
    """启动 Chrome 进程（带远程调试端口）。

    Args:
        port: 远程调试端口。
        headless: 是否无头模式。
        user_data_dir: 用户数据目录（Profile 隔离）。
        chrome_bin: Chrome 可执行文件路径。

    Returns:
        Chrome 子进程。

    Raises:
        FileNotFoundError: 未找到 Chrome。
    """
    if not chrome_bin:
        chrome_bin = find_chrome()
    if not chrome_bin:
        raise FileNotFoundError("未找到 Chrome，请设置 CHROME_BIN 环境变量或安装 Chrome")

    args = [
        chrome_bin,
        f"--remote-debugging-port={port}",
        *STEALTH_ARGS,
    ]

    if headless:
        args.append("--headless=new")

    if user_data_dir:
        args.append(f"--user-data-dir={user_data_dir}")

    # 代理
    proxy = os.getenv("XHS_PROXY")
    if proxy:
        args.append(f"--proxy-server={proxy}")
        logger.info("使用代理: %s", _mask_proxy(proxy))

    logger.info("启动 Chrome: port=%d, headless=%s", port, headless)
    process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 等待 Chrome 准备就绪
    _wait_for_chrome(port)
    return process


def close_chrome(process: subprocess.Popen) -> None:
    """关闭 Chrome 进程。"""
    if process.poll() is not None:
        return

    try:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        process.kill()
        process.wait(timeout=3)

    logger.info("Chrome 进程已关闭")


def is_chrome_running(port: int = DEFAULT_PORT) -> bool:
    """检查指定端口的 Chrome 是否在运行。"""
    import requests

    try:
        resp = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
        return resp.status_code == 200
    except (requests.ConnectionError, requests.Timeout):
        return False


def _wait_for_chrome(port: int, timeout: float = 15.0) -> None:
    """等待 Chrome 调试端口就绪。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_chrome_running(port):
            logger.info("Chrome 已就绪 (port=%d)", port)
            return
        time.sleep(0.5)
    logger.warning("等待 Chrome 就绪超时 (port=%d)", port)


def _mask_proxy(proxy_url: str) -> str:
    """隐藏代理 URL 中的敏感信息。"""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(proxy_url)
        if parsed.username:
            return proxy_url.replace(parsed.username, "***").replace(parsed.password or "", "***")
    except Exception:
        pass
    return proxy_url
