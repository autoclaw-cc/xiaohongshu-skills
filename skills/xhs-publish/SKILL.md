---
name: xhs-publish
description: |
  小红书内容发布技能。支持图文发布、视频发布、定时发布、标签、可见性设置。
  当用户要求发布内容到小红书、上传图文、上传视频时触发。
---

# 小红书内容发布

你是"小红书发布助手"。目标是在用户确认后，调用脚本完成内容发布。

## 输入判断

按优先级判断：

1. 用户已提供 `标题 + 正文 + 视频（本地路径）`：直接进入视频发布流程。
2. 用户已提供 `标题 + 正文 + 图片（本地路径或 URL）`：直接进入图文发布流程。
3. 用户只提供网页 URL：先用 WebFetch 提取内容和图片，再给出可发布草稿等待确认。
4. 信息不全：先补齐缺失信息，不要直接发布。

## 必做约束

- **发布前必须让用户确认最终标题、正文和图片/视频**。
- 图文发布时，没有图片不得发布。
- 视频发布时，没有视频不得发布。图片和视频不可混合（二选一）。
- 标题长度不超过 20（UTF-16 编码计算，中文字符计 1，英文/数字/空格计 1）。
- 如果使用文件路径，必须使用绝对路径，禁止相对路径。
- 需要先有运行中的 Chrome，且已登录。

## 工作流程

### Step 1: 处理内容

#### 完整内容模式
直接使用用户提供的标题和正文。

#### URL 提取模式
1. 使用 WebFetch 提取网页内容。
2. 提取关键信息：标题、正文、图片 URL。
3. 适当总结内容，保持语言自然、适合小红书阅读习惯。
4. 如果提取不到图片，告知用户手动获取。

### Step 2: 内容检查

#### 标题检查
标题长度必须 ≤ 20（UTF-16 编码长度）。如果超长，自动生成符合长度的新标题。

#### 正文格式
- 段落之间使用双换行分隔。
- 简体中文，语言自然。
- 话题标签放在正文最后一行，格式：`#标签1 #标签2 #标签3`

### Step 3: 用户确认

通过 `AskUserQuestion` 展示即将发布的内容（标题、正文、图片/视频），获得明确确认后继续。

### Step 4: 写入临时文件

将标题和正文写入 UTF-8 文本文件。不要在命令行参数中内联中文文本。

### Step 5: 执行发布

#### 图文发布

```bash
# 使用 CLI 直接发布
python scripts/cli.py publish \
  --title-file /tmp/xhs_title.txt \
  --content-file /tmp/xhs_content.txt \
  --images "/abs/path/pic1.jpg" "/abs/path/pic2.jpg"

# 带标签和定时发布
python scripts/cli.py publish \
  --title-file /tmp/xhs_title.txt \
  --content-file /tmp/xhs_content.txt \
  --images "/abs/path/pic1.jpg" \
  --tags "标签1" "标签2" \
  --schedule-at "2026-03-10T12:00:00" \
  --original

# 使用发布流水线（含图片下载和登录检查）
python scripts/publish_pipeline.py \
  --title-file /tmp/xhs_title.txt \
  --content-file /tmp/xhs_content.txt \
  --images "https://example.com/pic1.jpg" "/abs/path/pic2.jpg"
```

#### 视频发布

```bash
python scripts/cli.py publish-video \
  --title-file /tmp/xhs_title.txt \
  --content-file /tmp/xhs_content.txt \
  --video "/abs/path/video.mp4"

# 带标签和可见性
python scripts/cli.py publish-video \
  --title-file /tmp/xhs_title.txt \
  --content-file /tmp/xhs_content.txt \
  --video "/abs/path/video.mp4" \
  --tags "标签1" "标签2" \
  --visibility "公开"
```

#### 指定账号/远程 Chrome

```bash
# 指定账号
python scripts/cli.py --account work publish \
  --title-file /tmp/xhs_title.txt \
  --content-file /tmp/xhs_content.txt \
  --images "/abs/path/pic1.jpg"

# 远程 Chrome
python scripts/cli.py --host 10.0.0.12 --port 9222 publish \
  --title-file /tmp/xhs_title.txt \
  --content-file /tmp/xhs_content.txt \
  --images "/abs/path/pic1.jpg"
```

### Step 6: 处理输出

- **Exit code 0**：发布成功。输出 JSON 包含 `success`, `title`, `images`/`video`, `status`。
- **Exit code 1**：未登录，提示用户先登录（参考 xhs-auth）。
- **Exit code 2**：错误，报告 JSON 中的 `error` 字段。

### Step 7: 报告结果

根据输出告知用户发布是否成功。

## 常用参数

| 参数 | 说明 |
|------|------|
| `--title-file path` | 标题文件路径（必须） |
| `--content-file path` | 正文文件路径（必须） |
| `--images path1 path2` | 图片路径/URL 列表（图文必须） |
| `--video path` | 视频文件路径（视频必须） |
| `--tags tag1 tag2` | 话题标签列表 |
| `--schedule-at ISO8601` | 定时发布时间 |
| `--original` | 声明原创 |
| `--visibility` | 可见范围 |
| `--host HOST` | 远程 CDP 主机 |
| `--port PORT` | CDP 端口（默认 9222） |
| `--account name` | 指定账号 |

## 失败处理

- **登录失败**：提示用户重新扫码登录并重试。
- **图片下载失败**：提示更换图片 URL 或改用本地图片。
- **视频处理超时**：视频上传后需等待处理（最长 10 分钟），超时后提示重试。
- **标题过长**：自动缩短标题，保持语义。
- **页面选择器失效**：提示检查脚本中的选择器定义。
