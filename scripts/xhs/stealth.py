"""反检测配置：UA / Client Hints / JS 注入 / Chrome 启动参数。

关键原则：UA、navigator.platform、Client Hints、WebGL 等所有信号必须与实际平台一致。
"""

from __future__ import annotations

import platform as _platform

# Chrome 版本号 — 定期更新以匹配主流版本（当前对应 2025 年中期稳定版）
_CHROME_VER = "136"
_CHROME_FULL_VER = "136.0.0.0"


def _build_platform_config() -> dict:
    """根据实际操作系统生成一致的 UA / Client Hints / WebGL 配置。"""
    system = _platform.system()

    brands = [
        {"brand": "Chromium", "version": _CHROME_VER},
        {"brand": "Google Chrome", "version": _CHROME_VER},
        {"brand": "Not-A.Brand", "version": "24"},
    ]
    full_version_list = [
        {"brand": "Chromium", "version": _CHROME_FULL_VER},
        {"brand": "Google Chrome", "version": _CHROME_FULL_VER},
        {"brand": "Not-A.Brand", "version": "24.0.0.0"},
    ]

    if system == "Darwin":
        arch = "arm" if _platform.machine() == "arm64" else "x86"
        return {
            "ua": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{_CHROME_FULL_VER} Safari/537.36"
            ),
            "nav_platform": "MacIntel",
            "ua_metadata": {
                "brands": brands,
                "fullVersionList": full_version_list,
                "platform": "macOS",
                "platformVersion": "14.5.0",
                "architecture": arch,
                "model": "",
                "mobile": False,
                "bitness": "64",
                "wow64": False,
            },
            "webgl_vendor": "Apple Inc.",
            "webgl_renderer": (
                "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)"
            ),
        }

    if system == "Windows":
        return {
            "ua": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{_CHROME_FULL_VER} Safari/537.36"
            ),
            "nav_platform": "Win32",
            "ua_metadata": {
                "brands": brands,
                "fullVersionList": full_version_list,
                "platform": "Windows",
                "platformVersion": "15.0.0",
                "architecture": "x86",
                "model": "",
                "mobile": False,
                "bitness": "64",
                "wow64": False,
            },
            "webgl_vendor": "Google Inc. (Intel)",
            "webgl_renderer": (
                "ANGLE (Intel, Intel(R) UHD Graphics 630 (CML GT2), Direct3D11)"
            ),
        }

    # Linux
    return {
        "ua": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{_CHROME_FULL_VER} Safari/537.36"
        ),
        "nav_platform": "Linux x86_64",
        "ua_metadata": {
            "brands": brands,
            "fullVersionList": full_version_list,
            "platform": "Linux",
            "platformVersion": "6.5.0",
            "architecture": "x86",
            "model": "",
            "mobile": False,
            "bitness": "64",
            "wow64": False,
        },
        "webgl_vendor": "Google Inc. (Mesa)",
        "webgl_renderer": (
            "ANGLE (Mesa, Mesa Intel(R) UHD Graphics 630 (CML GT2), OpenGL 4.6)"
        ),
    }


PLATFORM_CONFIG = _build_platform_config()

# 向后兼容导出
REALISTIC_UA = PLATFORM_CONFIG["ua"]


def build_ua_override(chrome_full_ver: str | None = None) -> dict:
    """构建 Emulation.setUserAgentOverride 参数。

    Args:
        chrome_full_ver: Chrome 完整版本号（如 "134.0.6998.88"），
                         从 CDP /json/version 接口获取。为 None 时使用默认值。

    Returns:
        可直接传给 Emulation.setUserAgentOverride 的参数字典。
    """
    ver = chrome_full_ver or _CHROME_FULL_VER
    major = ver.split(".")[0]
    system = _platform.system()

    brands = [
        {"brand": "Chromium", "version": major},
        {"brand": "Google Chrome", "version": major},
        {"brand": "Not-A.Brand", "version": "24"},
    ]
    full_version_list = [
        {"brand": "Chromium", "version": ver},
        {"brand": "Google Chrome", "version": ver},
        {"brand": "Not-A.Brand", "version": "24.0.0.0"},
    ]

    if system == "Darwin":
        arch = "arm" if _platform.machine() == "arm64" else "x86"
        ua = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{ver} Safari/537.36"
        )
        nav_platform = "MacIntel"
        ua_platform = "macOS"
        platform_ver = "14.5.0"
    elif system == "Windows":
        arch = "x86"
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{ver} Safari/537.36"
        )
        nav_platform = "Win32"
        ua_platform = "Windows"
        platform_ver = "15.0.0"
    else:
        arch = "x86"
        ua = (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{ver} Safari/537.36"
        )
        nav_platform = "Linux x86_64"
        ua_platform = "Linux"
        platform_ver = "6.5.0"

    return {
        "userAgent": ua,
        "platform": nav_platform,
        "userAgentMetadata": {
            "brands": brands,
            "fullVersionList": full_version_list,
            "platform": ua_platform,
            "platformVersion": platform_ver,
            "architecture": arch,
            "model": "",
            "mobile": False,
            "bitness": "64",
            "wow64": False,
        },
    }

# ---------------------------------------------------------------------------
# 反检测 JS 脚本模板（$$占位符$$ 由 Python 替换为平台值）
# ---------------------------------------------------------------------------
_STEALTH_JS_TEMPLATE = """
(() => {
    // 1. navigator.webdriver — Proxy 包装原始 native getter，toString() 仍返回 [native code]
    const wd = Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver');
    if (wd && wd.get) {
        Object.defineProperty(Navigator.prototype, 'webdriver', {
            get: new Proxy(wd.get, { apply: () => false }),
            configurable: true,
        });
    }

    // 2. chrome.runtime
    if (!window.chrome) window.chrome = {};
    if (!window.chrome.runtime) {
        window.chrome.runtime = { connect: () => {}, sendMessage: () => {} };
    }

    // 3. chrome.app — headless 缺失此对象，检测脚本会检查
    if (!window.chrome.app) {
        window.chrome.app = {
            isInstalled: false,
            InstallState: {
                DISABLED: 'disabled',
                INSTALLED: 'installed',
                NOT_INSTALLED: 'not_installed',
            },
            RunningState: {
                CANNOT_RUN: 'cannot_run',
                READY_TO_RUN: 'ready_to_run',
                RUNNING: 'running',
            },
            getDetails: function() {},
            getIsInstalled: function() {},
            installState: function() { return 'not_installed'; },
            runningState: function() { return 'cannot_run'; },
        };
    }

    // 4. navigator.vendor — Chrome 应返回 "Google Inc."
    Object.defineProperty(navigator, 'vendor', {
        get: () => 'Google Inc.',
        configurable: true,
    });

    // 5. plugins — 不覆盖，真实 Chrome 已有正确的 PluginArray

    // 4. languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['zh-CN', 'zh', 'en-US', 'en'],
        configurable: true,
    });

    // 5. permissions
    const originalQuery = window.navigator.permissions?.query;
    if (originalQuery) {
        window.navigator.permissions.query = (parameters) =>
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters);
    }

    // 6. WebGL vendor/renderer — 与平台一致（同时覆盖 WebGL1 和 WebGL2）
    const overrideWebGL = (proto) => {
        const original = proto.getParameter;
        proto.getParameter = function(p) {
            if (p === 37445) return '$$WEBGL_VENDOR$$';
            if (p === 37446) return '$$WEBGL_RENDERER$$';
            return original.call(this, p);
        };
    };
    overrideWebGL(WebGLRenderingContext.prototype);
    if (typeof WebGL2RenderingContext !== 'undefined') {
        overrideWebGL(WebGL2RenderingContext.prototype);
    }

    // 7. hardwareConcurrency — 随机 4 或 8
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => [4, 8][Math.floor(Math.random() * 2)],
        configurable: true,
    });

    // 8. deviceMemory — 随机 4 或 8
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => [4, 8][Math.floor(Math.random() * 2)],
        configurable: true,
    });

    // 9. navigator.connection — 伪造网络信息
    Object.defineProperty(navigator, 'connection', {
        get: () => ({
            effectiveType: '4g',
            downlink: 10,
            rtt: 50,
            saveData: false,
        }),
        configurable: true,
    });

    // 10. chrome.csi / chrome.loadTimes — 空函数伪装
    if (window.chrome) {
        window.chrome.csi = function() { return {}; };
        window.chrome.loadTimes = function() { return {}; };
    }

    // 11. outerWidth/outerHeight — 不覆盖
    // 正常浏览器 outer > inner（有标题栏/工具栏），设为相等反而暴露自动化特征

})();
"""

STEALTH_JS = (
    _STEALTH_JS_TEMPLATE
    .replace("$$WEBGL_VENDOR$$", PLATFORM_CONFIG["webgl_vendor"])
    .replace("$$WEBGL_RENDERER$$", PLATFORM_CONFIG["webgl_renderer"])
)

# Chrome 启动参数（反检测相关）
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-component-update",
    "--disable-extensions",
    "--disable-sync",
]
