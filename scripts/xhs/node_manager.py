"""笔记管理功能 - 查询个人笔记列表、删除笔记。"""

from __future__ import annotations

import json
import logging
import time

from .cdp import Page
from .types import DeleteNoteResult, NoteManagerItem, NoteManagerList
from .urls import NOTE_MANAGER_URL

logger = logging.getLogger(__name__)

# 从 DOM 提取笔记列表 - 针对小红书创作者平台优化
_EXTRACT_NOTES_FROM_DOM_JS = r"""
(() => {
    const notes = [];
    
    // 使用 class="note" 选择器（根据实际 HTML 结构）
    const noteElements = document.querySelectorAll('.note');
    
    noteElements.forEach((note, index) => {
        const titleEl = note.querySelector('.title');
        const title = titleEl ? titleEl.textContent.trim() : '';
        
        const timeEl = note.querySelector('.time');
        const status = timeEl ? timeEl.textContent.trim() : '';
        
        const coverEl = note.querySelector('.media-bg, img.content');
        let coverUrl = '';
        if (coverEl) {
            const bgStyle = coverEl.getAttribute('style');
            if (bgStyle && bgStyle.includes('background-image')) {
                const match = bgStyle.match(/url\(["']?([^"')]+)["']?\)/);
                if (match) coverUrl = match[1];
            } else {
                coverUrl = coverEl.src || coverEl.getAttribute('data-src') || '';
            }
        }
        
        // 提取互动数据（顺序：浏览、评论、点赞、收藏、分享）
        let viewCount = '', commentCount = '', likeCount = '', collectCount = '', shareCount = '';
        const icons = note.querySelectorAll('.icon_list .icon');
        icons.forEach((icon, idx) => {
            const span = icon.querySelector('span');
            const count = span ? span.textContent.trim() : '';
            if (idx === 0) viewCount = count;
            else if (idx === 1) commentCount = count;
            else if (idx === 2) likeCount = count;
            else if (idx === 3) collectCount = count;
            else if (idx === 4) shareCount = count;
        });
        
        // 从 note-info 属性提取 noteId
        // 注意：note-info="[object Object]" 是 Vue 绑定，需要从 data-v- 属性或 impression 中提取
        let noteId = '';
        
        // 方法 1: 从 impression 属性提取（包含 noteId）
        const impression = note.getAttribute('data-impression');
        if (impression) {
            try {
                const impData = JSON.parse(impression);
                if (impData.noteTarget && impData.noteTarget.value && impData.noteTarget.value.noteId) {
                    noteId = impData.noteTarget.value.noteId;
                }
            } catch (e) {}
        }
        
        // 方法 2: 从 show-top 等属性判断状态
        const showTop = note.getAttribute('show-top');
        const showPerm = note.getAttribute('show-perm');
        
        if (title) {
            notes.push({
                noteId,
                title,
                coverUrl,
                status,
                viewCount,
                commentCount,
                likeCount,
                collectCount,
                shareCount,
                isTop: showTop === 'true',
            });
        }
    });
    
    return JSON.stringify({ notes, count: notes.length });
})()
"""

# 删除笔记 - 点击删除按钮（使用 noteId）
_JS_CLICK_DELETE = r"""
(() => {
    const noteId = '%s';
    
    // 查找包含 noteId 的笔记元素
    const noteElements = document.querySelectorAll('.note');
    for (const note of noteElements) {
        // 从 data-impression 属性提取 noteId
        const impression = note.getAttribute('data-impression');
        if (impression) {
            try {
                const impData = JSON.parse(impression);
                const currentId = impData.noteTarget?.value?.noteId || '';
                if (currentId === noteId) {
                    // 查找删除按钮（control data-del）
                    const deleteBtn = note.querySelector('.control.data-del, .data-del');
                    if (deleteBtn) {
                        deleteBtn.click();
                        return { success: true, action: 'clicked_delete_btn' };
                    }
                    return { success: false, error: '未找到删除按钮' };
                }
            } catch (e) {}
        }
    }
    
    return { success: false, error: '未找到笔记：' + noteId };
})()
"""

# 删除笔记 - 确认删除
_JS_CONFIRM_DELETE = r"""
(() => {
    // 查找确认弹窗
    const dialogs = document.querySelectorAll('.d-dialog, .modal, [role="dialog"], .popup, .confirm-dialog');
    for (const dialog of dialogs) {
        // 查找确认按钮
        const buttons = dialog.querySelectorAll('button, .d-button, [role="button"]');
        for (const btn of buttons) {
            const text = btn.textContent || btn.innerText || '';
            // 查找"确认"或"确定"按钮
            if (text.includes('确认') || text.includes('确定')) {
                btn.click();
                return { success: true, action: 'confirmed' };
            }
        }
    }
    
    // 如果没有找到弹窗，可能删除操作不需要确认
    return { success: true, action: 'no_confirm_needed' };
})()
"""

# 检查删除是否成功（使用 noteId）
_JS_CHECK_DELETE_RESULT = r"""
(() => {
    const noteId = '%s';
    const noteElements = document.querySelectorAll('.note');
    
    for (const note of noteElements) {
        const impression = note.getAttribute('data-impression');
        if (impression) {
            try {
                const impData = JSON.parse(impression);
                const currentId = impData.noteTarget?.value?.noteId || '';
                if (currentId === noteId) {
                    return { exists: true };
                }
            } catch (e) {}
        }
    }
    
    return { exists: false };
})()
"""


def list_notes(
    page: Page,
    note_type: str = "",
    status: str = "",
    keyword: str = "",
) -> NoteManagerList:
    """获取个人笔记列表。

    Args:
        page: CDP 页面对象。
        note_type: 笔记类型筛选："" (不限), "video" (视频), "normal" (图文)。
        status: 状态筛选："" (不限), "published" (已发布), "draft" (草稿) 等。
        keyword: 关键词筛选。

    Returns:
        NoteManagerList: 笔记列表及分页信息。
    """
    # 导航到笔记管理页面
    logger.info("导航到笔记管理页面：%s", NOTE_MANAGER_URL)
    page.navigate(NOTE_MANAGER_URL)
    page.wait_for_load()
    page.wait_dom_stable()
    time.sleep(3)  # 等待页面数据加载

    # 应用筛选条件（暂未实现）
    _apply_filters(page, note_type, status, keyword)

    # 从 DOM 提取笔记列表
    logger.info("从 DOM 提取笔记列表...")
    dom_result = page.evaluate(_EXTRACT_NOTES_FROM_DOM_JS)
    if dom_result:
        try:
            dom_data = json.loads(dom_result)
            logger.info("从 DOM 提取到 %d 条笔记", dom_data.get("count", 0))
            if dom_data.get("notes"):
                # 将 DOM 数据转换为 NoteManagerItem
                notes = []
                for item in dom_data["notes"]:
                    notes.append(NoteManagerItem(
                        note_id=item.get("noteId", ""),
                        title=item.get("title", ""),
                        cover_url=item.get("coverUrl", ""),
                        status=item.get("status", ""),
                        view_count=item.get("viewCount", ""),
                        comment_count=item.get("commentCount", ""),
                        like_count=item.get("likeCount", ""),
                        collect_count=item.get("collectCount", ""),
                        share_count=item.get("shareCount", ""),
                    ))
                return NoteManagerList(notes=notes, total=len(notes))
        except json.JSONDecodeError as e:
            logger.warning("解析 DOM 数据失败：%s", e)

    return NoteManagerList()


def delete_note(page: Page, note_id: str) -> DeleteNoteResult:
    """删除笔记。

    Args:
        page: CDP 页面对象。
        note_id: 笔记 ID（唯一标识）。

    Returns:
        DeleteNoteResult: 删除结果。
    """
    try:
        # 导航到笔记管理页面
        logger.info("导航到笔记管理页面：%s", NOTE_MANAGER_URL)
        page.navigate(NOTE_MANAGER_URL)
        page.wait_for_load()
        page.wait_dom_stable()
        time.sleep(3)

        # 步骤 1: 点击删除按钮
        logger.info("尝试删除笔记：%s", note_id)
        click_result = page.evaluate(_JS_CLICK_DELETE % note_id)
        logger.info("点击删除按钮结果：%s", click_result)
        
        if isinstance(click_result, str):
            click_result = json.loads(click_result)
        
        if not click_result.get('success'):
            return DeleteNoteResult(
                success=False,
                message=f"未找到笔记：{note_id}",
            )
        
        time.sleep(0.5)

        # 步骤 2: 确认删除
        logger.info("确认删除...")
        confirm_result = page.evaluate(_JS_CONFIRM_DELETE)
        logger.info("确认删除结果：%s", confirm_result)
        
        if isinstance(confirm_result, str):
            confirm_result = json.loads(confirm_result)
        
        time.sleep(1.5)

        # 步骤 3: 检查删除是否成功
        check_result = page.evaluate(_JS_CHECK_DELETE_RESULT % note_id)
        logger.info("检查删除结果：%s", check_result)
        
        if isinstance(check_result, str):
            check_result = json.loads(check_result)
        
        if check_result.get('exists', True):
            return DeleteNoteResult(
                success=False,
                message="删除后笔记仍然存在，可能删除失败",
            )

        return DeleteNoteResult(
            success=True,
            message=f"成功删除笔记：{note_id}",
        )
        
    except Exception as e:
        logger.error(f"删除笔记失败：%s", e, exc_info=True)
        return DeleteNoteResult(
            success=False,
            message=str(e),
        )


def _apply_filters(page: Page, note_type: str, status: str, keyword: str) -> None:
    """应用筛选条件。"""
    # TODO: 根据实际页面元素实现筛选逻辑
    # 目前页面加载后显示全部笔记，筛选功能需要进一步分析页面结构
    pass
