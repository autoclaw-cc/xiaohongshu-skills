"""Headless Chrome launcher for XHS Skills.

In place of the bridge_server + Chrome extension path, this module spawns a
local headless Chromium with --remote-debugging-port=9222 and reuses the
existing ``cdp.Browser`` class to obtain a ``Page`` object. Cookies persist in
``~/.xhs/chrome-profile/`` between invocations, so a successful login only
needs to happen once.

Usage:

    from headless_launcher import ensure_browser, ensure_page
    browser = ensure_browser()                 # idempotent: spawns only if 9222 is closed
    page = ensure_page(browser)                # opens about:blank tab, ready for use
    page.navigate("https://www.xiaohongshu.com")
    ...

Why this design: the project's core modules (feeds.py, search.py,
feed_detail.py, comment.py, publish.py, login.py, ...) all depend on
``xhs.cdp.Page`` and never reference the bridge. Only ``cli.py`` touches
``xhs.bridge``. By keeping the cdp.Browser -> cdp.Page contract identical to
the bridge path, no business module needs to change.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── Configuration ──────────────────────────────────────────────────────────

HOST = "127.0.0.1"
PORT = int(os.environ.get("XHS_CDP_PORT", "9222"))
LOCK_PATH = Path(os.environ.get("XHS_LAUNCHER_LOCK", "/tmp/xhs/headless.lock"))
PROFILE_DIR = Path(
    os.environ.get(
        "XHS_CHROME_PROFILE",
        str(Path.home() / ".xhs" / "chrome-profile"),
    )
)

# Hide HeadlessChrome from the UA; many sites (XHS included) detect it.
CHROME_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

# Headless flags. Kept in sync with anti-detection best practice for Chromium 120+:
#   --headless=new     → use the modern headless backend (renders normally)
#   --no-sandbox       → required when running as root or in unprivileged containers
#   --disable-blink-features=AutomationControlled → strips navigator.webdriver flag
#   --disable-dev-shm-usage → avoid /dev/shm OOM in containerized environments
#   --window-size      → make viewport realistic (XHS layouts assume 1280+)
CHROME_FLAGS = [
    "--headless=new",
    "--no-sandbox",
    "--no-zygote",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-namespace-sandbox",
    "--disable-setuid-sandbox",
    "--window-size=1366,768",
    f"--user-agent={CHROME_UA}",
    "--lang=zh-CN",
    "--accept-lang=zh-CN,zh;q=0.9,en;q=0.8",
]


# ─── Chrome binary resolution ───────────────────────────────────────────────


def _find_chromium() -> str:
    """Locate a usable Chromium binary.

    Order: $XHS_CHROMIUM_BIN > /snap/bin/chromium > google-chrome >
    chromium-browser > chromium. Returns absolute path or raises FileNotFoundError.
    """
    candidates: list[str] = []
    env_bin = os.environ.get("XHS_CHROMIUM_BIN")
    if env_bin:
        candidates.append(env_bin)
    candidates += [
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/opt/google/chrome/chrome",
        "/snap/bin/chromium",  # Last resort: snap chromium may fail with cap_dac_override in unprivileged containers
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise FileNotFoundError(
        "No chromium binary found. Set XHS_CHROMIUM_BIN or install "
        "snap chromium / google-chrome / chromium-browser."
    )


# ─── Port / process management ──────────────────────────────────────────────


def _port_is_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _cdp_is_ready(host: str, port: int, timeout: float = 1.0) -> bool:
    """True only if the port answers AND it speaks the CDP /json/version protocol."""
    import requests  # local import: dependency may not be on path before uv sync

    try:
        r = requests.get(f"http://{host}:{port}/json/version", timeout=timeout)
        return r.ok and "webSocketDebuggerUrl" in r.json()
    except Exception:
        return False


def _acquire_lock() -> None:
    """Best-effort single-launcher lock. Stale lock auto-expires after 60s."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            age = time.time() - LOCK_PATH.stat().st_mtime
            if age < 60:
                logger.debug("Headless launcher lock held (age=%.1fs), skipping launch", age)
                return
            logger.info("Stale launcher lock (age=%.1fs), removing", age)
        except OSError:
            pass
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass
    LOCK_PATH.write_text(str(os.getpid()))


# ─── Public API ─────────────────────────────────────────────────────────────


def ensure_browser(host: str = HOST, port: int = PORT) -> "CDPClient":
    """Return a connected ``cdp.CDPClient``. Spawn headless Chromium if needed.

    Idempotent: if another process already opened 9222 (e.g. a previous CLI
    invocation that was killed without cleaning up), we just attach to it.
    """
    from xhs.cdp import CDPClient  # noqa: F401  (import side effect: register)

    if _cdp_is_ready(host, port):
        logger.info("Reusing existing headless Chromium on %s:%s", host, port)
    else:
        _acquire_lock()
        _spawn_chromium(host, port)
        # Wait up to 15s for CDP endpoint to come up.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if _cdp_is_ready(host, port):
                break
            time.sleep(0.3)
        else:
            raise RuntimeError(
                f"Chromium did not open CDP endpoint at {host}:{port} within 15s"
            )

    cdp = CDPClient.__new__(CDPClient)
    cdp._ws = None  # placeholder; real connect done by Browser.connect()
    return cdp


def _spawn_chromium(host: str, port: int) -> None:
    binary = _find_chromium()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        binary,
        *CHROME_FLAGS,
        f"--remote-debugging-port={port}",
        f"--remote-debugging-address={host}",
        f"--user-data-dir={PROFILE_DIR}",
        "about:blank",
    ]
    logger.info("Launching headless Chromium: %s", " ".join(cmd))
    # New session group so we can SIGTERM the whole tree; stdout/stderr to log.
    log_path = PROFILE_DIR.parent / "chrome.log"
    log_fh = open(log_path, "ab")
    subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=log_fh,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def ensure_page(browser=None) -> "Page":
    """Open a single tab and return a connected ``cdp.Page``.

    Mirrors the bridge path's contract: a single Page is created and returned
    in a "ready to navigate" state. If ``browser`` is None, ensures one first.
    """
    from xhs.cdp import Browser, CDPClient

    if browser is None:
        browser = Browser(host=HOST, port=PORT)
        browser.connect()
    page = browser.new_page()
    return page


def shutdown_browser() -> None:
    """Best-effort shutdown of the headless Chromium (used by wrapper script).

    Three-tier strategy, because Chrome can be in any of these states:
      1. CDP is up (normal case) — use ``Browser.close`` which sends
         ``Browser.close`` over CDP. Clean.
      2. CDP is down but the process is still alive (zombie: SingletonLock
         held, port unreachable, gpu/network subprocs alive but the main
         browser has wedged). Try to kill the process group via SIGTERM,
         since we used ``start_new_session=True`` at spawn time so the
         browser is its own session leader.
      3. Nothing to do.
    """
    import os
    import signal
    import subprocess

    if not _port_is_open(HOST, PORT):
        # Try a process-group kill before giving up — handles zombie state.
        # Look for the headless Chromium master process and SIGTERM its
        # whole pgroup.
        try:
            r = subprocess.run(
                ["pgrep", "-f", "google-chrome.*--headless=new.*chrome-profile"],
                capture_output=True, text=True, timeout=3,
            )
            for pid in [int(x) for x in r.stdout.split() if x.strip().isdigit()]:
                try:
                    pgid = os.getpgid(pid)
                    os.killpg(pgid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        return

    # CDP is up: try CDP's /json/close first, then fall back to process kill.
    try:
        import requests
        requests.get(f"http://{HOST}:{PORT}/json/close", timeout=2)
    except Exception:
        pass
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("xhs.cdp").setLevel(logging.INFO)
    page = ensure_page()
    page.navigate("https://www.xiaohongshu.com")
    page.wait_for_load()
    title = page.evaluate("document.title")
    print(f"OK: page.title = {title!r}")
