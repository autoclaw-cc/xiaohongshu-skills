"""小红书评论/回复通知读取。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from .cdp import Page
from .human import sleep_random

logger = logging.getLogger(__name__)

NOTIFICATIONS_URL = "https://www.xiaohongshu.com/notification"

# 提取 Vue store 中 mentions 列表的 JS 脚本
# 参数：maxItems（正整数，0=不限）
_EXTRACT_JS = """
(function(maxItems) {
    try {
        const mentions = window.__INITIAL_STATE__.notification.notificationMap.mentions;
        const list = mentions.messageList;
        const n = maxItems > 0 ? Math.min(list.length, maxItems) : list.length;
        const items = [];
        for (let i = 0; i < n; i++) {
            const m = list[i];
            items.push({
                id: String(m.id || ''),
                type: String(m.type || ''),
                time: Number(m.time || 0),
                title: String(m.title || ''),
                userNickname: String(m.userInfo?.nickname || ''),
                userId: String(m.userInfo?.userid || ''),
                userAvatar: String(m.userInfo?.image || ''),
                userXsecToken: String(m.userInfo?.xsecToken || ''),
                commentId: String(m.commentInfo?.id || ''),
                commentContent: String(m.commentInfo?.content || ''),
                targetCommentContent: String(m.commentInfo?.targetComment?.content || ''),
                noteId: String(m.itemInfo?.id || ''),
                noteTitle: String(m.itemInfo?.content || ''),
                noteXsecToken: String(m.itemInfo?.xsecToken || ''),
            });
        }
        return JSON.stringify({
            hasMore: Boolean(mentions.hasMore),
            cursor: String(mentions.cursor || ''),
            totalLoaded: list.length,
            items: items,
        });
    } catch(e) {
        return JSON.stringify({error: e.message});
    }
})(%d)
"""


@dataclass
class NotificationUser:
    user_id: str = ""
    nickname: str = ""
    avatar: str = ""
    xsec_token: str = ""

    def to_dict(self) -> dict:
        return {
            "userId": self.user_id,
            "nickname": self.nickname,
            "avatar": self.avatar,
        }


@dataclass
class NotificationItem:
    id: str = ""
    type: str = ""            # "comment/item"（评论笔记）| "comment/comment"（回复评论）
    action: str = ""          # 人类可读："评论了你的笔记" / "回复了你的评论"
    time: int = 0             # Unix 时间戳
    comment_id: str = ""
    comment_content: str = ""
    quoted_comment: str = ""  # 被回复的原始评论（comment/comment 类型才有）
    user: NotificationUser = field(default_factory=NotificationUser)
    note_id: str = ""
    note_title: str = ""
    note_xsec_token: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "action": self.action,
            "time": self.time,
            "commentId": self.comment_id,
            "content": self.comment_content,
            "quotedComment": self.quoted_comment,
            "user": self.user.to_dict(),
            "noteId": self.note_id,
            "noteTitle": self.note_title,
            "noteXsecToken": self.note_xsec_token,
        }


@dataclass
class NotificationsResponse:
    items: list[NotificationItem] = field(default_factory=list)
    cursor: str = ""
    count: int = 0         # 本次返回条数
    total_loaded: int = 0  # 页面已加载总条数（每批最多20，hasMore=True 时有更多）
    has_more: bool = False

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "totalLoaded": self.total_loaded,
            "hasMore": self.has_more,
            "cursor": self.cursor,
            "items": [item.to_dict() for item in self.items],
        }


def get_notifications(
    page: Page,
    num: int = 20,
) -> NotificationsResponse:
    """获取评论/回复通知（每次最多20条，受页面单批加载限制）。

    导航到通知页面后从 Vue store 中读取已加载的数据。
    """
    page.navigate(NOTIFICATIONS_URL)
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(800, 1500)

    raw = page.evaluate(_EXTRACT_JS % int(num))

    if not raw:
        logger.warning("通知数据提取返回空")
        return NotificationsResponse()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("通知 JSON 解析失败: %s", e)
        return NotificationsResponse()

    if "error" in data:
        logger.error("JS 提取错误: %s", data["error"])
        return NotificationsResponse()

    items = [_parse_item(m) for m in data.get("items", [])]
    return NotificationsResponse(
        items=items,
        cursor=data.get("cursor", ""),
        count=len(items),
        total_loaded=data.get("totalLoaded", len(items)),
        has_more=data.get("hasMore", False),
    )


def _parse_item(m: dict) -> NotificationItem:
    """将 JS 提取的字典转为 NotificationItem。"""
    user = NotificationUser(
        user_id=m.get("userId", ""),
        nickname=m.get("userNickname", ""),
        avatar=m.get("userAvatar", ""),
        xsec_token=m.get("userXsecToken", ""),
    )
    return NotificationItem(
        id=m.get("id", ""),
        type=m.get("type", ""),
        action=m.get("title", ""),
        time=m.get("time", 0),
        comment_id=m.get("commentId", ""),
        comment_content=m.get("commentContent", ""),
        quoted_comment=m.get("targetCommentContent", ""),
        user=user,
        note_id=m.get("noteId", ""),
        note_title=m.get("noteTitle", ""),
        note_xsec_token=m.get("noteXsecToken", ""),
    )
