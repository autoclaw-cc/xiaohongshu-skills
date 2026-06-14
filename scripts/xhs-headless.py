#!/usr/bin/env python3
"""XHS Headless CLI wrapper.

在无 GUI 的 Linux 服务器上,包装 scripts/cli.py,做两件事:
1. 透传所有子命令和参数给 cli.py。
2. 如果 cli.py 输出了 qrcode_path 字段,把二维码 PNG 额外复制到
   ~/.hermes/skills/xiaohongshu-skills/.qrcode.png(便于 agent / 用户读取)。

用法:
  python scripts/xhs-headless.py check-login
  python scripts/xhs-headless.py login
  python scripts/xhs-headless.py search-feeds --keyword "测试"
  python scripts/xhs-headless.py --shutdown    # 关闭 headless Chromium
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ─── 路径常量 ──────────────────────────────────────────────────────────────

SCRIPTS_DIR = Path(__file__).resolve().parent
CLI_PY = SCRIPTS_DIR / "cli.py"
SKILL_ROOT = SCRIPTS_DIR.parent
QRCODE_HERO_PATH = SKILL_ROOT / ".qrcode.png"  # agent / 飞书会话来读取这个路径
SHUTDOWN_FLAG = Path(os.environ.get("XHS_LAUNCHER_LOCK", "/tmp/xhs/headless.lock"))


def main() -> int:
    if not CLI_PY.exists():
        print(json.dumps({"error": f"cli.py not found at {CLI_PY}"}), file=sys.stderr)
        return 2

    # ── 关闭模式 ──────────────────────────────────────────────────
    if "--shutdown" in sys.argv[1:]:
        return _shutdown()

    # ── 透传给 cli.py ─────────────────────────────────────────────
    cmd = [sys.executable, str(CLI_PY), *sys.argv[1:]]
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    # Don't capture: cli.py writes JSON to stdout and we want to surface it as-is
    # so downstream tools (jq, scripts) work unchanged.
    proc = subprocess.run(cmd, env=env, check=False)
    rc = proc.returncode

    # ── 副作用:把最新二维码复制到 .qrcode.png(如果存在) ────────
    # cli.py 在 login 流程里把二维码写到 /tmp/xhs/login_qrcode.png
    # (见 scripts/xhs/login.py:_QR_FILE)。我们复制它到 skill 根目录的
    # 隐藏文件,让 agent / 飞书消息能直接读到这个绝对路径。
    for candidate in (
        Path("/tmp/xhs/login_qrcode.png"),
        Path(tempfile_gettempdir_login_qrcode()),
    ):
        if candidate.exists():
            try:
                shutil.copy2(candidate, QRCODE_HERO_PATH)
                # 写一行单独到 stderr,不影响 cli.py 的 JSON stdout
                print(
                    f"[xhs-headless] qrcode copied to {QRCODE_HERO_PATH}",
                    file=sys.stderr,
                )
            except OSError as e:
                print(f"[xhs-headless] failed to copy qrcode: {e}", file=sys.stderr)
            break

    return rc


def tempfile_gettempdir_login_qrcode() -> str:
    import tempfile

    return os.path.join(tempfile.gettempdir(), "xhs", "login_qrcode.png")


def _shutdown() -> int:
    """关闭 headless Chromium。"""
    import headless_launcher  # scripts/headless_launcher.py, 同 package 兄弟

    try:
        headless_launcher.shutdown_browser()
        # 额外:清理 lock 文件 + profile 目录外的临时文件
        try:
            SHUTDOWN_FLAG.unlink(missing_ok=True)
        except OSError:
            pass
        print(json.dumps({"ok": True, "message": "headless Chromium shut down"}))
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
