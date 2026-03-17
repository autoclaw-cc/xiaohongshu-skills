"""小红书风控/限流检测。"""

from __future__ import annotations

from .cdp import Page


def detect_rate_limit(page: Page) -> dict | None:
    """Check if current page is a xiaohongshu rate-limit/security verification page.

    Returns:
        {"type": "rate_limit"|"security_verify", "message": str} or None if page is normal.
    """
    url = page.evaluate("document.URL") or ""
    if "website-login/captcha" in url:
        title = page.evaluate("document.title") or ""
        body = page.evaluate("document.body.innerText.substring(0, 300)") or ""
        if "Requests too frequent" in body or "请求过于频繁" in body:
            return {"type": "rate_limit", "message": "IP被小红书限流，请配置代理"}
        if "Security Verification" in title or "Scan with" in body:
            return {"type": "security_verify", "message": "IP安全验证，请配置代理"}
    return None
