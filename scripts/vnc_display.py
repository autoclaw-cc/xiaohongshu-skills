"""虚拟显示 + VNC + noVNC 管理。

在无图形界面的服务器上启动 Xvfb 虚拟显示 + Xvnc + noVNC，
让 Chrome 以非 headless 模式运行，并通过浏览器远程查看。

用法：
    python scripts/vnc_display.py start   # 启动虚拟显示 + VNC + noVNC
    python scripts/vnc_display.py stop    # 停止所有服务
    python scripts/vnc_display.py status  # 查看状态
    python scripts/vnc_display.py url     # 输出 noVNC 访问地址
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("vnc_display")

# 配置
DISPLAY_NUM = 99
DISPLAY = f":{DISPLAY_NUM}"
SCREEN_SIZE = "1920x1080x24"
VNC_PORT = 5999          # Xvnc VNC port
NOVNC_PORT = 6080        # noVNC web port
NOVNC_DIR = "/opt/noVNC"
WEBSOCKIFY = os.path.join(NOVNC_DIR, "utils", "websockify", "run")
PID_DIR = Path.home() / ".xhs" / "vnc"


def _pid_file(name: str) -> Path:
    return PID_DIR / f"{name}.pid"


def _save_pid(name: str, pid: int) -> None:
    PID_DIR.mkdir(parents=True, exist_ok=True)
    _pid_file(name).write_text(str(pid))


def _load_pid(name: str) -> int | None:
    f = _pid_file(name)
    if f.exists():
        try:
            return int(f.read_text().strip())
        except ValueError:
            pass
    return None


def _is_running(name: str) -> bool:
    pid = _load_pid(name)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill(name: str) -> bool:
    pid = _load_pid(name)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        # Wait for process to die
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(0.3)
            except OSError:
                break
        # Force kill if still alive
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    except OSError:
        pass
    _pid_file(name).unlink(missing_ok=True)
    return True


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except (ConnectionRefusedError, TimeoutError, OSError):
            return False


def start_xvfb() -> int | None:
    """启动 Xvfb 虚拟显示。"""
    if _is_running("xvfb"):
        logger.info("Xvfb 已在运行")
        return _load_pid("xvfb")

    proc = subprocess.Popen(
        [
            "Xvfb", DISPLAY,
            "-screen", "0", SCREEN_SIZE,
            "-ac",       # disable access control
            "+extension", "GLX",
            "+render",
            "-noreset",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1)
    if proc.poll() is not None:
        logger.error("Xvfb 启动失败")
        return None

    _save_pid("xvfb", proc.pid)
    logger.info("Xvfb 已启动: DISPLAY=%s, PID=%d", DISPLAY, proc.pid)
    return proc.pid


def start_xvnc() -> int | None:
    """启动 Xvnc (TigerVNC) 连接到 Xvfb 显示。

    用 x0vncserver 连接已有的 X display，而不是启动新的 Xvnc。
    如果没有 x0vncserver，回退到 Xvnc 替换 Xvfb。
    """
    if _is_running("xvnc"):
        logger.info("VNC server 已在运行")
        return _load_pid("xvnc")

    import shutil

    # 方案1: 使用 x0vncserver 附加到已有 Xvfb display
    x0vnc = shutil.which("x0vncserver")
    if x0vnc:
        proc = subprocess.Popen(
            [
                x0vnc,
                "-display", DISPLAY,
                "-rfbport", str(VNC_PORT),
                "-SecurityTypes", "None",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "DISPLAY": DISPLAY},
        )
        time.sleep(1)
        if proc.poll() is not None:
            logger.warning("x0vncserver 启动失败，尝试 Xvnc 方案")
        else:
            _save_pid("xvnc", proc.pid)
            logger.info("x0vncserver 已启动: port=%d, PID=%d", VNC_PORT, proc.pid)
            return proc.pid

    # 方案2: 直接用 Xvnc 替换 Xvfb（先停掉 Xvfb）
    xvnc = shutil.which("Xvnc")
    if xvnc:
        _kill("xvfb")
        time.sleep(0.5)

        proc = subprocess.Popen(
            [
                xvnc, DISPLAY,
                "-geometry", "1920x1080",
                "-depth", "24",
                "-rfbport", str(VNC_PORT),
                "-SecurityTypes", "None",
                "-ac",
                "+extension", "GLX",
                "+render",
                "-RandR",  # disable RandR auto-resize from VNC clients
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)
        if proc.poll() is not None:
            logger.error("Xvnc 启动失败")
            return None

        _save_pid("xvnc", proc.pid)
        # Also save as xvfb since Xvnc replaces it
        _save_pid("xvfb", proc.pid)
        logger.info("Xvnc 已启动 (替换 Xvfb): DISPLAY=%s, VNC port=%d, PID=%d",
                     DISPLAY, VNC_PORT, proc.pid)
        return proc.pid

    logger.error("未找到 x0vncserver 或 Xvnc")
    return None


def start_novnc() -> int | None:
    """启动 noVNC websocket 代理。"""
    if _is_running("novnc"):
        logger.info("noVNC 已在运行")
        return _load_pid("novnc")

    if not os.path.exists(NOVNC_DIR):
        logger.error("noVNC 未安装: %s", NOVNC_DIR)
        return None

    # Use websockify directly
    websockify_bin = shutil.which("websockify")
    if not websockify_bin:
        websockify_bin = WEBSOCKIFY

    import shutil as _shutil

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "websockify",
            "--web", NOVNC_DIR,
            str(NOVNC_PORT),
            f"localhost:{VNC_PORT}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1)
    if proc.poll() is not None:
        # Fallback: try the noVNC launch script
        launch_sh = os.path.join(NOVNC_DIR, "utils", "novnc_proxy")
        if os.path.exists(launch_sh):
            proc = subprocess.Popen(
                [
                    launch_sh,
                    "--vnc", f"localhost:{VNC_PORT}",
                    "--listen", str(NOVNC_PORT),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(2)
            if proc.poll() is not None:
                logger.error("noVNC 启动失败")
                return None

    _save_pid("novnc", proc.pid)
    logger.info("noVNC 已启动: http://localhost:%d/vnc.html, PID=%d", NOVNC_PORT, proc.pid)
    return proc.pid


def start() -> dict:
    """启动完整的虚拟显示 + VNC + noVNC 栈。"""
    result = {"success": False}

    # 1. Start Xvfb first (may be replaced by Xvnc)
    xvfb_pid = start_xvfb()

    # 2. Start VNC server (may replace Xvfb with Xvnc)
    vnc_pid = start_xvnc()
    if vnc_pid is None:
        result["error"] = "VNC server 启动失败"
        return result

    # 3. Set DISPLAY for this process and children
    os.environ["DISPLAY"] = DISPLAY

    # 4. Install websockify if needed and start noVNC
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "websockify"],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass

    novnc_pid = start_novnc()
    if novnc_pid is None:
        result["error"] = "noVNC 启动失败"
        return result

    # 5. Get server IP for access URL
    hostname = socket.gethostname()
    try:
        ip = socket.gethostbyname(hostname)
    except Exception:
        ip = "localhost"

    result.update({
        "success": True,
        "display": DISPLAY,
        "vnc_port": VNC_PORT,
        "novnc_port": NOVNC_PORT,
        "novnc_url": f"http://{ip}:{NOVNC_PORT}/vnc.html?autoconnect=true",
        "novnc_url_local": f"http://localhost:{NOVNC_PORT}/vnc.html?autoconnect=true",
        "pids": {
            "xvfb": _load_pid("xvfb"),
            "xvnc": _load_pid("xvnc"),
            "novnc": _load_pid("novnc"),
        },
    })
    return result


def stop() -> dict:
    """停止所有虚拟显示服务。"""
    stopped = []
    for name in ["novnc", "xvnc", "xvfb"]:
        if _kill(name):
            stopped.append(name)
    os.environ.pop("DISPLAY", None)
    return {"success": True, "stopped": stopped}


def status() -> dict:
    """检查服务状态。"""
    services = {}
    for name in ["xvfb", "xvnc", "novnc"]:
        pid = _load_pid(name)
        running = _is_running(name)
        services[name] = {"pid": pid, "running": running}

    display_set = os.environ.get("DISPLAY", "")

    hostname = socket.gethostname()
    try:
        ip = socket.gethostbyname(hostname)
    except Exception:
        ip = "localhost"

    return {
        "display": display_set,
        "services": services,
        "novnc_url": f"http://{ip}:{NOVNC_PORT}/vnc.html?autoconnect=true"
        if services["novnc"]["running"] else None,
        "all_running": all(s["running"] for s in services.values()),
    }


def get_display_env() -> str | None:
    """返回虚拟显示的 DISPLAY 值（如果正在运行）。

    供 chrome_launcher 使用，在启动 Chrome 前设置 DISPLAY 环境变量。
    """
    if _is_running("xvfb") or _is_running("xvnc"):
        return DISPLAY
    return None


# --- CLI ---
import shutil  # noqa: E402 (needed for start_novnc fallback)


def _output(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        print("用法: python vnc_display.py <start|stop|status|url>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "start":
        result = start()
        _output(result)
        sys.exit(0 if result["success"] else 1)
    elif cmd == "stop":
        _output(stop())
    elif cmd == "status":
        _output(status())
    elif cmd == "url":
        s = status()
        if s.get("novnc_url"):
            print(s["novnc_url"])
        else:
            print("noVNC 未运行")
            sys.exit(1)
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
