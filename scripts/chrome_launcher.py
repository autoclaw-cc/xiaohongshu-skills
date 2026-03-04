"""Chrome 进程管理（跨平台），对应 Go browser/browser.go 的进程管理部分。"""

from __future__ import annotations

import json
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


def kill_chrome(port: int = DEFAULT_PORT) -> None:
    """关闭指定端口的 Chrome 实例。

    尝试通过 CDP Browser.close 命令关闭，失败则使用进程信号。

    Args:
        port: Chrome 调试端口。
    """
    import requests

    # 策略1: 通过 CDP 关闭
    try:
        resp = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
        if resp.status_code == 200:
            ws_url = resp.json().get("webSocketDebuggerUrl")
            if ws_url:
                import websockets.sync.client

                ws = websockets.sync.client.connect(ws_url)
                ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
                ws.close()
                logger.info("通过 CDP Browser.close 关闭 Chrome (port=%d)", port)
                time.sleep(1)
                return
    except Exception:
        pass

    # 策略2: 通过 lsof 查找并 kill 进程
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            import contextlib

            pids = result.stdout.strip().split("\n")
            for pid in pids:
                with contextlib.suppress(OSError, ValueError):
                    os.kill(int(pid), signal.SIGTERM)
            logger.info("通过 SIGTERM 关闭 Chrome 进程 (port=%d)", port)
            time.sleep(1)
            return
    except Exception:
        pass

    logger.warning("未能关闭 Chrome (port=%d)", port)


def restart_chrome(
    port: int = DEFAULT_PORT,
    headless: bool = False,
    user_data_dir: str | None = None,
    chrome_bin: str | None = None,
) -> subprocess.Popen:
    """重启 Chrome：关闭当前实例后以新模式重新启动。

    Args:
        port: 远程调试端口。
        headless: 是否无头模式。
        user_data_dir: 用户数据目录。
        chrome_bin: Chrome 可执行文件路径。

    Returns:
        新的 Chrome 子进程。
    """
    logger.info("重启 Chrome: port=%d, headless=%s", port, headless)
    kill_chrome(port)
    time.sleep(1)
    return launch_chrome(
        port=port,
        headless=headless,
        user_data_dir=user_data_dir,
        chrome_bin=chrome_bin,
    )


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
