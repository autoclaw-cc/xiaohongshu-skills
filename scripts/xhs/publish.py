"""图文发布，对应 Go xiaohongshu/publish.go（837 行）。"""

from __future__ import annotations

import json
import logging
import random
import re
import time

from .cdp import Page
from .errors import (
    AccountRiskControlError,
    ContentTooLongError,
    PublishError,
    TitleTooLongError,
    UploadTimeoutError,
)
from .selectors import (
    CONTENT_EDITOR,
    CONTENT_LENGTH_ERROR,
    CREATOR_TAB,
    DATETIME_INPUT,
    FILE_INPUT,
    IMAGE_PREVIEW,
    ORIGINAL_SWITCH,
    ORIGINAL_SWITCH_CARD,
    POPOVER,
    SCHEDULE_SWITCH,
    TAG_FIRST_ITEM,
    TAG_TOPIC_CONTAINER,
    TITLE_INPUT,
    TITLE_MAX_SUFFIX,
    UPLOAD_INPUT,
    VISIBILITY_DROPDOWN,
    VISIBILITY_OPTIONS,
)
from .types import PublishImageContent
from .urls import PUBLISH_URL

logger = logging.getLogger(__name__)


def publish_image_content(page: Page, content: PublishImageContent) -> None:
    """发布图文内容（填写表单 + 点击发布）。

    Args:
        page: CDP 页面对象。
        content: 发布内容。

    Raises:
        PublishError: 发布失败。
        UploadTimeoutError: 上传超时。
        TitleTooLongError: 标题超长。
        ContentTooLongError: 正文超长。
    """
    fill_publish_form(page, content)
    click_publish_button(page)


def fill_publish_form(page: Page, content: PublishImageContent) -> None:
    """填写图文发布表单，不点击发布按钮。

    Args:
        page: CDP 页面对象。
        content: 发布内容。

    Raises:
        PublishError: 填写失败。
        UploadTimeoutError: 上传超时。
        TitleTooLongError: 标题超长。
        ContentTooLongError: 正文超长。
    """
    if not content.image_paths:
        raise PublishError("图片不能为空")

    # 导航到发布页
    _navigate_to_publish_page(page)

    # 点击"上传图文" TAB
    _click_publish_tab(page, "上传图文")
    time.sleep(1)

    # 上传图片
    _upload_images(page, content.image_paths)

    # 标签截取
    tags = content.tags[:10] if len(content.tags) > 10 else content.tags
    if len(content.tags) > 10:
        logger.warning("标签数量超过10，截取前10个")

    logger.info(
        "发布内容: title=%s, images=%d, tags=%d, schedule=%s, original=%s, visibility=%s",
        content.title,
        len(content.image_paths),
        len(tags),
        content.schedule_time,
        content.is_original,
        content.visibility,
    )

    # 填写表单（不点击发布）
    _fill_publish_form(
        page,
        content.title,
        content.content,
        tags,
        content.schedule_time,
        content.is_original,
        content.visibility,
    )


def click_publish_button(page: Page) -> None:
    """触发发布。

    XHS 把发布/暂存按钮包成 <xhs-publish-btn> Vue web component + closed shadow DOM。
    host 上挂着 'publish' 自定义事件 ——直接 dispatchEvent 比坐标点击 + CDP 真鼠标
    更可靠：不依赖坐标、不依赖 chrome.debugger、不会被反爬的鼠标事件指纹检测。

    流程：
      1. 注入 3 层响应捕获（XHR/fetch 内容匹配 + console hook + DOM toast 观察器）
      2. host.dispatchEvent(new CustomEvent('publish')) 触发发布
      3. 轮询任一层捕获到的结果，识别业务错误码（-9136 = 账号风控）

    Raises:
        PublishError: 未找到 host 或按钮被禁用。
        AccountRiskControlError: 账号被风控（如 code=-9136）。
    """
    # 1) 注入多层响应捕获
    #    XHS 自家有多层 XHR 拦截嵌套（s1-main / interceptor.js / axios），URL pattern
    #    无法可靠匹配；用"响应内容标志"判定 + console.error hook + DOM toast 观察器三管齐下。
    page.evaluate(
        """
        (() => {
            window.__xhsPublishResult = null;

            // 内容标志：成功响应含 note_id；失败响应含 HTTPBizError 或 -913x 风控码或"禁止发笔记"
            function looksLikePublishResp(body) {
                if (!body) return false;
                return body.includes('HTTPBizError')
                    || /"code":\\s*-913\\d/.test(body)
                    || body.includes('禁止发笔记')
                    || /"note_id":\\s*"[^"]+"/.test(body);
            }

            function capture(source, info) {
                if (window.__xhsPublishResult) return;  // first-wins
                window.__xhsPublishResult = {source, ...info};
            }

            function captureFromBody(source, url, status, body) {
                if (window.__xhsPublishResult) return;
                if (!looksLikePublishResp(body)) return;
                try {
                    const j = JSON.parse(body);
                    capture(source, {url, status, ...j});
                } catch (e) {
                    // body 不是顶层 JSON，尝试 regex 抽取 code/msg
                    const codeMatch = body.match(/"code":\\s*(-?\\d+)/);
                    const msgMatch = body.match(/"msg":\\s*"([^"]+)"/);
                    capture(source, {
                        url, status,
                        code: codeMatch ? parseInt(codeMatch[1], 10) : null,
                        msg: msgMatch ? msgMatch[1] : null,
                        raw: body.slice(0, 500),
                    });
                }
            }

            // Layer 1: XHR — 内容标志判定，绕开 URL pattern 不可靠的问题
            const origOpen = XMLHttpRequest.prototype.open;
            const origSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {
                this.__xhsPubUrl = url;
                return origOpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function() {
                this.addEventListener('loadend', () => {
                    captureFromBody('xhr', this.__xhsPubUrl, this.status, this.responseText || '');
                });
                return origSend.apply(this, arguments);
            };

            // Layer 2: fetch — 同样内容判定
            const origFetch = window.fetch;
            window.fetch = async function(...args) {
                const url = typeof args[0] === 'string' ? args[0] : args[0].url;
                const resp = await origFetch.apply(this, args);
                try {
                    const txt = await resp.clone().text();
                    captureFromBody('fetch', url, resp.status, txt);
                } catch (e) {}
                return resp;
            };

            // Layer 3: console hook — XHS publish module 失败时打印 "[发布失败] HTTPBizError: ..."
            ['error', 'log', 'warn'].forEach((method) => {
                const orig = console[method];
                console[method] = function(...args) {
                    const txt = args.map((a) => {
                        if (typeof a === 'string') return a;
                        if (a && typeof a === 'object') {
                            try { return JSON.stringify(a); } catch (e) { return String(a); }
                        }
                        return String(a);
                    }).join(' ');
                    if (txt.includes('HTTPBizError') || txt.includes('发布失败')) {
                        const codeMatch = txt.match(/"code":\\s*(-?\\d+)/);
                        const msgMatch = txt.match(/"msg":\\s*"([^"]+)"/);
                        if (codeMatch) {
                            capture('console', {
                                code: parseInt(codeMatch[1], 10),
                                msg: msgMatch ? msgMatch[1] : '发布失败',
                                raw_console: txt.slice(0, 800),
                            });
                        }
                    }
                    return orig.apply(this, args);
                };
            });

            // Layer 4: DOM MutationObserver — toast 出现"违反/违规/禁止发笔记"等关键词
            const RISK_KW = /违反|违规|禁止发笔记|审核未通过|账号异常|无法发布/;
            const obs = new MutationObserver((muts) => {
                if (window.__xhsPublishResult) return;
                muts.forEach((m) => {
                    m.addedNodes.forEach((n) => {
                        if (n.nodeType !== 1) return;
                        const txt = (n.textContent || '').trim();
                        if (txt.length === 0 || txt.length > 300) return;
                        if (!RISK_KW.test(txt)) return;
                        capture('toast', {
                            code: -9136,  // 默认风控码
                            msg: txt,
                            cls: (n.className || '').toString().slice(0, 80),
                        });
                    });
                });
            });
            obs.observe(document.body, {childList: true, subtree: true});
            window.__xhsPublishObs = obs;
        })()
        """
    )

    # 2) dispatchEvent 触发发布
    fire_result = page.evaluate(
        """
        (() => {
            const host = document.querySelector('xhs-publish-btn[is-publish="true"]');
            if (!host) return 'not_found';
            if (host.getAttribute('submit-disabled') === 'true') return 'disabled';
            host.dispatchEvent(new CustomEvent('publish', {bubbles: true, cancelable: true}));
            return 'fired';
        })()
        """
    )
    if fire_result == "not_found":
        raise PublishError("未找到 <xhs-publish-btn> 发布按钮容器")
    if fire_result == "disabled":
        raise PublishError("发布按钮 submit-disabled=true，不可发布")

    # 3) 轮询等待 publish API 响应（15s 超时）
    deadline = time.monotonic() + 15
    result = None
    while time.monotonic() < deadline:
        result = page.evaluate("window.__xhsPublishResult")
        if result:
            break
        time.sleep(0.3)

    if not result:
        logger.warning("15s 内未捕获到任何发布反馈（XHR/console/toast 都没匹配）")
        time.sleep(2)
        return

    # 4) 解析业务码
    source = result.get("source", "unknown")
    code = result.get("code")
    msg = result.get("msg", "")
    success = result.get("success")
    logger.info("捕获发布响应（来源=%s code=%s msg=%r）", source, code, msg)

    if code == 0 or success is True:
        logger.info("发布成功")
        time.sleep(2)
        return

    # XHS 风控类业务码（-913x 段）+ 关键词兜底
    is_risk_control = (
        (code is not None and -9140 <= code <= -9130)
        or "违反" in (msg or "")
        or "禁止发笔记" in (msg or "")
        or "违规" in (msg or "")
    )
    if is_risk_control:
        raise AccountRiskControlError(code or -9136, msg or "账号被风控")

    raise PublishError(f"发布失败：code={code} msg={msg!r}")


def save_as_draft(page: Page) -> None:
    """点击「暂存离开」按钮保存草稿。"""
    clicked = page.evaluate(
        """
        (() => {
            const buttons = document.querySelectorAll('button.custom-button');
            for (const btn of buttons) {
                if (btn.textContent.trim() === '暂存离开') {
                    btn.click();
                    return true;
                }
            }
            return false;
        })()
        """
    )
    if clicked:
        time.sleep(2)
        logger.info("已点击「暂存离开」，内容已保存到草稿箱")
    else:
        logger.warning("未找到「暂存离开」按钮")
        raise PublishError("未找到「暂存离开」按钮")


# ========== 页面导航 ==========


def _navigate_to_publish_page(page: Page) -> None:
    """导航到发布页面。"""
    page.navigate(PUBLISH_URL)
    page.wait_for_load(timeout=300)
    time.sleep(3)
    page.wait_dom_stable()
    time.sleep(2)


def _click_publish_tab(page: Page, tab_name: str) -> None:
    """点击发布页 TAB（上传图文/上传视频/写长文/发播客）。

    XHS 创作中心反爬升级版 —— 每个 tab 渲染 3 份：

    1. "屏外诱饵"：Vue scoped (data-v-*)，藏在 `left:-9999px; top:-9999px`，
       看起来像真 tab 但**不绑事件**，点了 active 不切换。**没有 hp 属性**所以
       老代码会把它当真 tab 误点。
    2. "明显 honey pot"：带 data-hp-kind + button-hp-installed，
       inset 在视口内但 opacity:1e-05 + z-index:-1，点了被标机器人。
    3. **真 tab**：Vue scoped + 在视口内 + 带 **data-hp-bound="1"**
       （XHS 反爬框架"已绑定真实事件"的标记）。

    正确策略：找 `data-hp-bound="1"` 且无 hp 陷阱属性的 Vue tab；用 active class
    切换确认。

    """
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        found = page.evaluate(
            f"""
            (() => {{
                const name = {json.dumps(tab_name)};
                const tabs = document.querySelectorAll({json.dumps(CREATOR_TAB)});

                // 真 tab：有 data-hp-bound + 无 hp 陷阱属性 + 在视口里
                for (const t of tabs) {{
                    if (t.hasAttribute('data-hp-kind') ||
                        t.hasAttribute('button-hp-installed')) continue;
                    if (!t.hasAttribute('data-hp-bound')) continue;
                    const title = t.querySelector('span.title');
                    if (!title || title.textContent.trim() !== name) continue;
                    const r = t.getBoundingClientRect();
                    if (r.left < -1000 || r.top < -1000) continue;
                    t.click();
                    return 'clicked';
                }}

                // 兜底 1：无 hp 陷阱属性 + 在视口内（兼容 XHS 未来去掉 data-hp-bound）
                for (const t of tabs) {{
                    if (t.hasAttribute('data-hp-kind') ||
                        t.hasAttribute('button-hp-installed')) continue;
                    const title = t.querySelector('span.title');
                    if (!title || title.textContent.trim() !== name) continue;
                    const r = t.getBoundingClientRect();
                    if (r.left < -1000 || r.top < -1000) continue;
                    t.click();
                    return 'clicked';
                }}

                return 'not_found';
            }})()
            """
        )

        # 等待 active class 切换：检查真正 active 的 tab title 是否 == 目标
        if found == "clicked":
            switched_deadline = time.monotonic() + 5
            while time.monotonic() < switched_deadline:
                active_title = page.evaluate(
                    f"""
                    (() => {{
                        // 找视口内 + 无 hp 陷阱 + active 的 tab，取它的 title
                        const tabs = document.querySelectorAll({json.dumps(CREATOR_TAB)});
                        for (const t of tabs) {{
                            if (t.hasAttribute('data-hp-kind') ||
                                t.hasAttribute('button-hp-installed')) continue;
                            if (!t.classList.contains('active')) continue;
                            const r = t.getBoundingClientRect();
                            if (r.left < -1000 || r.top < -1000) continue;
                            const title = t.querySelector('span.title');
                            return title ? title.textContent.trim() : null;
                        }}
                        return null;
                    }})()
                    """
                )
                if active_title == tab_name:
                    return
                time.sleep(0.2)
            logger.warning(
                "点击了 %s 但 active tab 仍是 %r，可能被反爬拦截", tab_name, active_title
            )

        if found == "clicked":
            return

        if found == "blocked":
            # 尝试移除弹窗
            _remove_pop_cover(page)

        time.sleep(0.2)

    # 调试：输出页面信息
    debug_info = page.evaluate("""
        (() => {
            const creatorTabs = document.querySelectorAll('div.creator-tab');
            const tabTexts = Array.from(creatorTabs).map(t => ({
                text: t.textContent.trim(),
                html: t.outerHTML.substring(0, 200)
            }));
            const url = window.location.href;
            return JSON.stringify({url, tabCount: creatorTabs.length, tabs: tabTexts});
        })()
    """)
    logger.error("调试信息: %s", debug_info)
    raise PublishError(f"没有找到发布 TAB - {tab_name}")


def _remove_pop_cover(page: Page) -> None:
    """移除弹窗遮挡。"""
    if page.has_element(POPOVER):
        page.remove_element(POPOVER)
    # 点击空位置
    x = 380 + random.randint(0, 100)
    y = 20 + random.randint(0, 60)
    page.mouse_click(float(x), float(y))


# ========== 图片上传 ==========


def _upload_images(page: Page, image_paths: list[str]) -> None:
    """逐张上传图片。"""
    import os

    valid_paths = [p for p in image_paths if os.path.exists(p)]
    if not valid_paths:
        raise PublishError("没有有效的图片文件")

    for i, path in enumerate(valid_paths):
        selector = UPLOAD_INPUT if i == 0 else FILE_INPUT
        logger.info("上传第 %d 张图片: %s", i + 1, path)

        page.set_file_input(selector, [path])
        _wait_for_upload_complete(page, i + 1)
        time.sleep(1)


def _wait_for_upload_complete(page: Page, expected_count: int) -> None:
    """等待图片上传完成。"""
    max_wait = 60.0
    start = time.monotonic()

    while time.monotonic() - start < max_wait:
        count = page.get_elements_count(IMAGE_PREVIEW)
        if count >= expected_count:
            logger.info("图片上传完成: %d", count)
            return
        time.sleep(0.5)

    raise UploadTimeoutError(f"第{expected_count}张图片上传超时(60s)")


# ========== 表单提交 ==========


def _extract_hashtags_from_content(content: str, tags: list[str]) -> tuple[str, list[str]]:
    """从正文末尾提取 hashtag 行，合并到 tags 列表。

    Returns:
        (cleaned_content, merged_tags)
    """
    lines = content.rstrip().split("\n")
    # 检查最后一行是否全是 #tag 格式
    if lines:
        last_line = lines[-1].strip()
        hashtag_pattern = re.compile(r"^(#\S+\s*)+$")
        if hashtag_pattern.match(last_line):
            # 提取 hashtag
            extracted = re.findall(r"#(\S+)", last_line)
            # 合并到 tags（去重）
            existing = {t.lstrip("#") for t in tags}
            merged = list(tags)
            for t in extracted:
                if t not in existing:
                    merged.append(t)
                    existing.add(t)
            # 去掉最后一行
            cleaned = "\n".join(lines[:-1]).rstrip()
            logger.info("从正文末尾提取 %d 个标签，合并后共 %d 个", len(extracted), len(merged))
            return cleaned, merged
    return content, list(tags)


def _fill_publish_form(
    page: Page,
    title: str,
    content: str,
    tags: list[str],
    schedule_time: str | None,
    is_original: bool,
    visibility: str,
) -> None:
    """填写表单（不点击发布）。"""
    # 从正文末尾提取 hashtag 并合并到 tags
    content, tags = _extract_hashtags_from_content(content, tags)

    # 标题——填写前先校验长度，超限直接报错（由 AI 重新生成标题）
    from title_utils import calc_title_length

    title_len = calc_title_length(title)
    if title_len > 20:
        raise TitleTooLongError(str(title_len), "20")

    page.input_text(TITLE_INPUT, title)
    time.sleep(0.5)
    _check_title_max_length(page)
    logger.info("标题长度检查通过")
    time.sleep(1)

    # 正文
    content_selector = _find_content_element(page)
    page.input_content_editable(content_selector, content)

    # 回点标题（增强稳定性）
    time.sleep(1)
    page.click_element(TITLE_INPUT)
    logger.info("已回点标题输入框")

    # 标签
    if tags:
        _input_tags(page, content_selector, tags)
    time.sleep(1)
    _check_content_max_length(page)
    logger.info("正文长度检查通过")

    # 定时发布
    if schedule_time:
        _set_schedule_publish(page, schedule_time)

    # 可见范围
    _set_visibility(page, visibility)

    # 原创声明
    if is_original:
        try:
            _set_original(page)
            logger.info("已声明原创")
        except Exception as e:
            logger.warning("设置原创声明失败: %s", e)

    logger.info("表单填写完成，等待确认发布")


def _find_content_element(page: Page) -> str:
    """查找内容输入框（兼容两种 UI）。"""
    if page.has_element(CONTENT_EDITOR):
        return CONTENT_EDITOR

    if page.has_element('[role="textbox"][contenteditable="true"]'):
        return '[role="textbox"][contenteditable="true"]'

    # 查找带 placeholder 的 p 元素的 textbox 父元素
    found = page.evaluate(
        """
        (() => {
            const ps = document.querySelectorAll('p');
            for (const p of ps) {
                const placeholder = p.getAttribute('data-placeholder');
                if (placeholder && placeholder.includes('输入正文描述')) {
                    let current = p;
                    for (let i = 0; i < 5; i++) {
                        current = current.parentElement;
                        if (!current) break;
                        if (current.getAttribute('role') === 'textbox') {
                            return 'found';
                        }
                    }
                }
            }
            return '';
        })()
        """
    )
    if found == "found":
        return "[role='textbox']"

    raise PublishError("没有找到内容输入框")


def _check_title_max_length(page: Page) -> None:
    """检查标题长度是否超限。"""
    text = page.get_element_text(TITLE_MAX_SUFFIX)
    if text:
        parts = text.split("/")
        if len(parts) == 2:
            raise TitleTooLongError(parts[0], parts[1])
        raise TitleTooLongError(text, "?")


def _check_content_max_length(page: Page) -> None:
    """检查正文长度是否超限。"""
    text = page.get_element_text(CONTENT_LENGTH_ERROR)
    if text:
        parts = text.split("/")
        if len(parts) == 2:
            raise ContentTooLongError(parts[0], parts[1])
        raise ContentTooLongError(text, "?")


# ========== 标签输入 ==========


def _input_tags(page: Page, content_selector: str, tags: list[str]) -> None:
    """逐个插入话题，并在全部操作结束后确认每个话题仍然存在。"""
    tags = list(dict.fromkeys(tag.strip().lstrip("#") for tag in tags))
    if not tags or any(not tag for tag in tags):
        raise PublishError("话题名称不能为空")
    if len(tags) > 10:
        raise PublishError("话题数量不能超过 10 个")
    _focus_editor_end(page, content_selector)
    page.evaluate(
        f"""
        (() => {{
            const el = document.querySelector({json.dumps(content_selector)});
            if (!el) throw new Error('正文编辑器不存在');
            document.execCommand("insertParagraph", false, null);
        }})()
        """
    )
    time.sleep(0.5)
    for tag in tags:
        _input_single_tag(page, content_selector, tag)

    # 不再回到正文中间插入换行，避免事后改变编辑器选区及话题节点。
    _assert_topics_preserved(page, content_selector, tags)


def _read_editor_topics(page: Page, content_selector: str) -> list[dict]:
    """读取实际 Tiptap 话题元数据，不把隐藏的 [话题]# 后缀算入名称。"""
    result = page.evaluate(
        f"""
        (() => {{
            const el = document.querySelector({json.dumps(content_selector)});
            if (!el) throw new Error('正文编辑器不存在');
            return Array.from(el.querySelectorAll('[data-topic]')).map(node => {{
                try {{ return JSON.parse(node.getAttribute('data-topic')); }}
                catch (_) {{ return null; }}
            }}).filter(topic => topic && topic.id && topic.name);
        }})()
        """
    )
    if not isinstance(result, list):
        raise PublishError("无法读取编辑器话题元数据")
    return result


def _assert_topics_preserved(page: Page, content_selector: str, tags: list[str]) -> list[dict]:
    topics = _read_editor_topics(page, content_selector)
    names = [topic["name"] for topic in topics]
    if any(names.count(tag) != 1 for tag in tags):
        raise PublishError("表单话题缺失或重复，已停止发布")
    logger.info("全部话题保留确认: %s", ", ".join(tags))
    return topics


def _focus_editor_end(page: Page, content_selector: str) -> None:
    """重新聚焦正文编辑器，并把光标放到全部内容末尾。"""
    focused = page.evaluate(
        f"""
        (() => {{
            const el = document.querySelector({json.dumps(content_selector)});
            if (!el) return false;
            el.focus();
            const range = document.createRange();
            range.selectNodeContents(el);
            range.collapse(false);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
            return true;
        }})()
        """
    )
    if not focused:
        raise PublishError("标签输入时无法重新定位正文光标")


def _get_editor_tag_state(page: Page, content_selector: str, tag: str) -> dict[str, object]:
    """读取当前标签在编辑器中的结构状态。"""
    result = page.evaluate(
        f"""
        (() => {{
            const el = document.querySelector({json.dumps(content_selector)});
            if (!el) return null;
            const expected = {json.dumps(tag)};
            const candidates = Array.from(el.querySelectorAll('[data-topic]')).filter((node) => {{
                try {{
                    const topic = JSON.parse(node.getAttribute('data-topic'));
                    return topic.name === expected && Boolean(topic.id);
                }} catch (_) {{ return false; }}
            }});
            return {{
                html: el.innerHTML,
                text: el.innerText || el.textContent || '',
                candidateHtml: [...new Set(candidates.map((node) => node.outerHTML))]
            }};
        }})()
        """
    )
    return result if isinstance(result, dict) else {}


def _tag_was_committed(before: dict[str, object], after: dict[str, object], tag: str) -> bool:
    """判断点击联想后，普通文本是否变成了当前标签的独立编辑器节点。"""
    before_html = str(before.get("html", ""))
    after_html = str(after.get("html", ""))
    after_text = str(after.get("text", ""))
    before_candidates = before.get("candidateHtml", [])
    after_candidates = after.get("candidateHtml", [])
    return (
        bool(after_html)
        and after_html != before_html
        and f"#{tag}" in after_text
        and isinstance(after_candidates, list)
        and bool(after_candidates)
        and after_candidates != before_candidates
    )


def _find_matching_tag_suggestion_position(page: Page, tag: str) -> int:
    """精确匹配候选名称，标记要真实点击的卡片并返回一基位置。"""
    result = page.evaluate(
        f"""
        (() => {{
            const container = document.querySelector({json.dumps(TAG_TOPIC_CONTAINER)});
            if (!container) return 0;
            const expected = {json.dumps(tag)};
            const normalize = (value) => (value || '').trim().replace(/^#\\s*/, '');
            const items = Array.from(container.querySelectorAll({json.dumps(TAG_FIRST_ITEM)}));
            items.forEach((item) => item.removeAttribute('data-codex-tag-target'));
            for (let index = 0; index < items.length; index++) {{
                const item = items[index];
                const texts = [
                    item.querySelector('.name')?.textContent || '',
                    ...(item.innerText || '').split(/\\r?\\n/),
                    ...Array.from(item.childNodes)
                        .filter((node) => node.nodeType === Node.TEXT_NODE)
                        .map((node) => node.textContent || ''),
                    ...Array.from(item.querySelectorAll('*'))
                        .filter((node) => node.children.length === 0)
                        .map((node) => node.textContent || '')
                ];
                if (texts.some((text) => normalize(text) === expected)) {{
                    item.setAttribute('data-codex-tag-target', 'true');
                    return index + 1;
                }}
            }}
            return 0;
        }})()
        """
    )
    return int(result) if isinstance(result, (int, float)) else 0


def _input_single_tag(page: Page, content_selector: str, tag: str) -> None:
    """输入单个标签。"""
    # 每轮都重新定位光标。点击上一条联想后焦点可能留在下拉层，不能依赖浏览器状态。
    _focus_editor_end(page, content_selector)

    # 继续当前未完成的话题时，不重复插入同一段 hashtag。
    pending = page.evaluate(
        f"""(() => {{
            const el = document.querySelector({json.dumps(content_selector)});
            const node = el.querySelector('[data-decoration-id].suggestion');
            return node ? node.textContent : '';
        }})()"""
    )
    if pending != f"#{tag}":
        page.type_text("#", delay_ms=0)
        time.sleep(0.3)
        for char in tag:
            page.type_text(char, delay_ms=0)
            time.sleep(random.uniform(0.05, 0.12))

    before_commit = _get_editor_tag_state(page, content_selector, tag)

    # 话题搜索有网络延迟，等待同名结果，不重新输入或重复提交。
    deadline = time.monotonic() + 20.0
    clicked = False
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if page.has_element(TAG_TOPIC_CONTAINER):
            item_selector = f"{TAG_TOPIC_CONTAINER} {TAG_FIRST_ITEM}"
            position = _find_matching_tag_suggestion_position(page, tag)
            if page.has_element(item_selector) and position > 0:
                target_selector = f'{TAG_TOPIC_CONTAINER} [data-codex-tag-target="true"]'
                selected = page.evaluate(
                    f"""(() => {{
                        const item = document.querySelector({json.dumps(target_selector)});
                        if (!item) return false;
                        const name = item.querySelector('.name') || item;
                        // 保留正文选区，不先 focus 或滚动浮层。
                        // Tiptap 候选需要 mousedown 阶段处理选区及话题提交。
                        for (const type of ['mousedown', 'mouseup', 'click']) {{
                            name.dispatchEvent(new MouseEvent(type, {{
                                bubbles: true, cancelable: true, button: 0
                            }}));
                        }}
                        return true;
                    }})()"""
                )
                if selected is not True:
                    raise PublishError(f"话题候选已消失: #{tag}")
                logger.info("选择同名话题: %s", tag)
                clicked = True
                break

    if not clicked:
        raise PublishError(f"未找到匹配的标签联想，停止发布: #{tag}")

    # 成功以实际 data-topic 名称及 ID 为准；装饰 span 和纯文本均不算。
    commit_deadline = time.monotonic() + 3.0
    while time.monotonic() < commit_deadline:
        after_commit = _get_editor_tag_state(page, content_selector, tag)
        if _tag_was_committed(before_commit, after_commit, tag):
            break
        time.sleep(0.2)
    else:
        raise PublishError(f"标签联想点击未生效，停止发布: #{tag}")

    # 把光标显式放到已提交话题之后；不能依赖点击后的焦点。
    _focus_editor_end(page, content_selector)
    page.type_text(" ", delay_ms=0)

    time.sleep(0.8)


# ========== 定时发布 ==========


def _set_schedule_publish(page: Page, schedule_time: str) -> None:
    """设置定时发布。"""
    from datetime import datetime

    # 解析 ISO8601 时间
    try:
        dt = datetime.fromisoformat(schedule_time)
    except ValueError as e:
        raise PublishError(f"定时发布时间格式错误: {e}") from e

    # 点击定时发布开关
    page.click_element(SCHEDULE_SWITCH)
    time.sleep(0.8)

    # 设置日期时间
    datetime_str = dt.strftime("%Y-%m-%d %H:%M")
    page.select_all_text(DATETIME_INPUT)
    page.input_text(DATETIME_INPUT, datetime_str)
    time.sleep(0.5)

    logger.info("已设置定时发布: %s", datetime_str)


# ========== 可见范围 ==========


def _set_visibility(page: Page, visibility: str) -> None:
    """设置可见范围。"""
    if not visibility or visibility == "公开可见":
        logger.info("可见范围: 公开可见（默认）")
        return

    supported = {"仅自己可见", "仅互关好友可见"}
    if visibility not in supported:
        raise PublishError(
            f"不支持的可见范围: {visibility}，支持: 公开可见、仅自己可见、仅互关好友可见"
        )

    # 点击下拉框
    page.click_element(VISIBILITY_DROPDOWN)
    time.sleep(0.5)

    # 查找并点击目标选项
    clicked = page.evaluate(
        f"""
        (() => {{
            const opts = document.querySelectorAll({json.dumps(VISIBILITY_OPTIONS)});
            for (const opt of opts) {{
                if (opt.textContent.includes({json.dumps(visibility)})) {{
                    opt.click();
                    return true;
                }}
            }}
            return false;
        }})()
        """
    )

    if not clicked:
        raise PublishError(f"未找到可见范围选项: {visibility}")

    logger.info("已设置可见范围: %s", visibility)
    time.sleep(0.2)


# ========== 原创声明 ==========


def _set_original(page: Page) -> None:
    """设置原创声明。"""
    # 查找原创声明卡片并点击开关
    result = page.evaluate(
        f"""
        (() => {{
            const cards = document.querySelectorAll({json.dumps(ORIGINAL_SWITCH_CARD)});
            for (const card of cards) {{
                if (!card.textContent.includes('原创声明')) continue;
                const sw = card.querySelector({json.dumps(ORIGINAL_SWITCH)});
                if (!sw) continue;
                const input = sw.querySelector('input[type="checkbox"]');
                if (input && input.checked) return 'already_on';
                sw.click();
                return 'clicked';
            }}
            return 'not_found';
        }})()
        """
    )

    if result == "already_on":
        logger.info("原创声明已开启")
        return

    if result == "not_found":
        raise PublishError("未找到原创声明选项")

    time.sleep(0.5)

    # 处理确认弹窗
    _confirm_original_declaration(page)


def _confirm_original_declaration(page: Page) -> None:
    """处理原创声明确认弹窗。"""
    time.sleep(0.8)

    # 勾选 checkbox
    page.evaluate(
        """
        (() => {
            const footers = document.querySelectorAll('div.footer');
            for (const footer of footers) {
                if (!footer.textContent.includes('原创声明须知')) continue;
                const cb = footer.querySelector('div.d-checkbox input[type="checkbox"]');
                if (cb && !cb.checked) cb.click();
                return;
            }
        })()
        """
    )
    time.sleep(0.5)

    # 点击声明原创按钮
    result = page.evaluate(
        """
        (() => {
            const footers = document.querySelectorAll('div.footer');
            for (const footer of footers) {
                if (!footer.textContent.includes('声明原创')) continue;
                const btn = footer.querySelector('button.custom-button');
                if (btn) {
                    if (btn.classList.contains('disabled') || btn.disabled) {
                        const cb = footer.querySelector('div.d-checkbox input[type="checkbox"]');
                        if (cb && !cb.checked) cb.click();
                        return 'button_disabled';
                    }
                    btn.click();
                    return 'clicked';
                }
            }
            return 'button_not_found';
        })()
        """
    )

    if result == "button_not_found":
        raise PublishError("未找到声明原创按钮")
    if result == "button_disabled":
        raise PublishError("声明原创按钮仍处于禁用状态")

    logger.info("已成功点击声明原创按钮")
    time.sleep(0.3)
