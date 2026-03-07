---
name: xhs-interact
description: |
  小红书社交互动技能。发表评论、回复评论、点赞、收藏。
  当用户要求评论、回复、点赞或收藏小红书帖子时触发。
version: 1.0.0
metadata:
  openclaw:
    requires:
      bins:
        - python3
        - uv
    emoji: "\U0001F4AC"
    os:
      - macos
      - linux
---

# 小红书社交互动

你是"小红书互动助手"。帮助用户在小红书上进行社交互动。

## 输入判断

按优先级判断：

1. 用户要求"发评论 / 评论这篇 / 写评论"：执行发表评论流程。
2. 用户要求"回复评论 / 回复 TA"：执行回复评论流程。
3. 用户要求"点赞 / 取消点赞"：执行点赞流程。
4. 用户要求"收藏 / 取消收藏"：执行收藏流程。

## 必做约束

- **评论和回复内容必须经过用户确认后才能发送**。
- 所有互动操作需要 `feed_id` 和 `xsec_token`（从搜索或详情中获取）。
- 评论文本不可为空。
- 点赞和收藏操作是幂等的（重复执行不会出错）。
- CLI 输出 JSON 格式。

## 工作流程

### 发表评论

1. 确认已有 `feed_id` 和 `xsec_token`（如没有，先搜索或获取详情）。
2. 向用户确认评论内容。
3. 执行发送。

```bash
python scripts/cli.py post-comment \
  --feed-id 67abc1234def567890123456 \
  --xsec-token XSEC_TOKEN \
  --content "写得很实用，感谢分享"
```

### 回复评论

回复指定评论或用户：

```bash
# 回复指定评论（通过评论 ID）
python scripts/cli.py reply-comment \
  --feed-id 67abc1234def567890123456 \
  --xsec-token XSEC_TOKEN \
  --content "谢谢你的分享" \
  --comment-id COMMENT_ID

# 回复指定用户（通过用户 ID）
python scripts/cli.py reply-comment \
  --feed-id 67abc1234def567890123456 \
  --xsec-token XSEC_TOKEN \
  --content "谢谢你的分享" \
  --user-id USER_ID
```

### 点赞 / 取消点赞

```bash
# 点赞
python scripts/cli.py like-feed \
  --feed-id 67abc1234def567890123456 \
  --xsec-token XSEC_TOKEN

# 取消点赞
python scripts/cli.py like-feed \
  --feed-id 67abc1234def567890123456 \
  --xsec-token XSEC_TOKEN \
  --unlike
```

### 收藏 / 取消收藏

```bash
# 收藏
python scripts/cli.py favorite-feed \
  --feed-id 67abc1234def567890123456 \
  --xsec-token XSEC_TOKEN

# 取消收藏
python scripts/cli.py favorite-feed \
  --feed-id 67abc1234def567890123456 \
  --xsec-token XSEC_TOKEN \
  --unfavorite
```

## 互动策略建议

当用户需要批量互动时，建议：

1. 先搜索目标内容（xhs-explore）。
2. 浏览搜索结果，选择要互动的笔记。
3. 获取详情确认内容。
4. 针对性地发表评论 / 点赞 / 收藏。
5. 每次互动之间保持合理间隔，避免频率过高。

## 失败处理

- **未登录**：提示先登录（参考 xhs-auth）。
- **笔记不可访问**：可能是私密或已删除笔记。
- **评论输入框未找到**：页面结构可能已变化，提示检查选择器。
- **评论发送失败**：检查内容是否包含敏感词。
- **点赞/收藏失败**：重试一次，仍失败则报告错误。
