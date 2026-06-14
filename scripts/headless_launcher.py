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

# Proxy: set XHS_CHROME_PROXY to e.g. "http://192.168.31.32:17899" to route
# all Chrome traffic through it. Required for Chrome 149 headless on
# networks that block googleapis.com (where GCM / SafeBrowsing / etc.
# services hang in retry loops and starve the CDP HTTP handler).
# The proxy-bypass-list keeps the CDP loopback (127.0.0.1 / ::1 / localhost)
# direct so we never go through the proxy to talk to ourselves.
PROXY_URL = os.environ.get("XHS_CHROME_PROXY", "").strip()

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
    """True if any loopback (127.0.0.1 or [::1]) accepts a TCP connect on port.

    Chrome 149 headless sometimes binds only IPv6; trying just the
    configured host misses it.
    """
    loopbacks: list[str] = [host, "127.0.0.1", "::1"]
    seen: set[str] = set()
    for h in loopbacks:
        if h in seen:
            continue
        seen.add(h)
        try:
            with socket.create_connection((h, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def _cdp_is_ready(host: str, port: int, timeout: float = 1.0) -> bool:
    """True only if the port answers AND it speaks the CDP /json/version protocol.

    Chrome 149 headless on Linux sometimes binds only the IPv6 loopback
    ([::1]:9222) even when ``--remote-debugging-address=127.0.0.1`` is
    passed (the flag is silently ignored in some builds). Try both.
    """
    import requests  # local import: dependency may not be on path before uv sync

    candidates: list[str] = []
    seen: set[str] = set()
    for h in (host, "127.0.0.1", "[::1]", "localhost"):
        if h in seen:
            continue
        seen.add(h)
        candidates.append(h)

    for h in candidates:
        url = f"http://{h}:{port}/json/version"
        try:
            r = requests.get(url, timeout=timeout)
            if r.ok and "webSocketDebuggerUrl" in r.json():
                return True
        except Exception:
            continue
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
    ]
    if PROXY_URL:
        # Outbound proxy: lets GCM / SafeBrowsing / etc. reach
        # googleapis.com so their retry loops terminate cleanly.
        cmd += [
            f"--proxy-server={PROXY_URL}",
            # CDP loopback must stay direct.
            "--proxy-bypass-list=<-loopback>,127.0.0.1,::1,localhost",
        ]
    else:
        # No proxy: blackhole the Google service hosts so connection
        # attempts fail FAST (TCP refused) instead of hanging on a
        # blocked SSL handshake. The GCM service then gives up
        # immediately and the libevent main loop stays free for CDP.
        # This avoids the chrome-149 headless GCM DEPRECATED_ENDPOINT
        # hang on networks that block googleapis.com.
        cmd += [
            "--host-rules="
            + ",".join([
                "MAP clients2.google.com 127.0.0.1",
                "MAP fcm.googleapis.com 127.0.0.1",
                "MAP *.googleapis.com 127.0.0.1",
                "MAP accounts.google.com 127.0.0.1",
                "MAP ssl.gstatic.com 127.0.0.1",
                "MAP clientservices.googleapis.com 127.0.0.1",
                "MAP optimizely.com 127.0.0.1",
            ]),
        ]
    cmd.append("about:blank")
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
    from xhs.cdp import Browser

    if browser is None:
        # Chrome 149 headless on Linux sometimes only binds [::1]:9222
        # even when --remote-debugging-address=127.0.0.1 is requested.
        # Probe both loopbacks and pick the one that actually works.
        chosen_host = _probe_working_cdp_host()
        if chosen_host is None:
            chosen_host = HOST  # fall back; cdp.Browser will fail loudly
        browser = Browser(host=chosen_host, port=PORT)
        browser.connect()
    page = browser.new_page()
    return page


def _probe_working_cdp_host() -> str | None:
    """Return the loopback that actually serves /json/version right now."""
    import requests

    for h in (HOST, "127.0.0.1", "[::1]", "localhost"):
        try:
            r = requests.get(f"http://{h}:{PORT}/json/version", timeout=1)
            if r.ok and "webSocketDebuggerUrl" in r.json():
                return h
        except Exception:
            continue
    return None


def shutdown_browser() -> None:
    """Best-effort shutdown of the headless Chromium (used by wrapper script).

    Tries in this order:
      1. CDP ``/json/close`` (graceful: closes all tabs, then quits)
      2. SIGTERM the whole process group via ``killpg`` (zombie or
         wedged-CDP fallback)
      3. Best-effort cleanup of the launcher-side /tmp/xhs/headless.lock
         and the per-profile SingletonLock (the next start will recreate
         them anyway)
    """
    import os
    import signal
    import subprocess

    # Step 1: try the graceful CDP shutdown first
    cdp_close_attempted = False
    try:
        import requests
        for h in (HOST, "127.0.0.1", "[::1]", "localhost"):
            try:
                requests.get(f"http://{h}:{PORT}/json/close", timeout=2)
                cdp_close_attempted = True
                break
            except Exception:
                continue
    except Exception:
        pass

    # Give the graceful path a few seconds to do its thing
    if cdp_close_attempted:
        time.sleep(2)

    # Step 2: if anything is still alive, SIGTERM the process group.
    # We avoid `pgrep -f` entirely because it has two nasty footguns:
    #   (a) on a pattern that starts with `--`, pgrep treats the pattern
    #       as a flag and fails to match anything
    #   (b) `pgrep -f` matches against the full command line, so pgrep
    #       itself (and the wrapper bash that ran the pattern) get
    #       matched — easy to SIGTERM the wrong process
    # Also, the binary path is /opt/google/chrome/chrome (no
    # "google-chrome" string), so we match on "chrome" as the binary
    # basename, anchored via the comm filter.
    # Instead, walk /proc directly and regex the cmdline bytes.
    if _port_is_open(HOST, PORT):
        import re as _re

        pattern = _re.compile(
            rf"--remote-debugging-port={PORT}.*--user-data-dir.*chrome-profile"
        )
        try:
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                pid = int(entry)
                if pid == os.getpid():
                    continue
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        data = f.read().replace(b"\x00", b" ").decode(
                            "utf-8", errors="replace"
                        )
                    if not pattern.search(data):
                        continue
                    with open(f"/proc/{pid}/comm") as f:
                        comm = f.read().strip()
                    # Defensive: only act on real Chrome master procs
                    if comm not in ("chrome", "chromium"):
                        continue
                    pgid = os.getpgid(pid)
                    os.killpg(pgid, signal.SIGTERM)
                except (OSError, ProcessLookupError, PermissionError):
                    continue
        except OSError:
            pass

    # Step 3: lock cleanup (next start recreates these anyway)
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass
    # SingletonLock is owned by Chrome, not by us; let Chrome clean it up
    # when the process actually exits. Don't touch it from here.


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("xhs.cdp").setLevel(logging.INFO)
    page = ensure_page()
    page.navigate("https://www.xiaohongshu.com")
    page.wait_for_load()
    title = page.evaluate("document.title")
    print(f"OK: page.title = {title!r}")
