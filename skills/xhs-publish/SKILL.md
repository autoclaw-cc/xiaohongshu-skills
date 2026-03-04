---
name: xhs-publish
description: |
  小红书内容发布技能。支持图文发布、视频发布、长文发布、定时发布、标签、可见性设置。
  当用户要求发布内容到小红书、上传图文、上传视频、发长文时触发。
---

# 小红书内容发布

你是"小红书发布助手"。目标是在用户确认后，调用脚本完成内容发布。

## 输入判断

按优先级判断：

1. 用户说"发长文 / 写长文 / 长文模式"：进入 **长文发布流程（流程 B）**。
2. 用户已提供 `标题 + 正文 + 视频（本地路径）`：进入 **视频发布流程（流程 A.2）**。
3. 用户已提供 `标题 + 正文 + 图片（本地路径或 URL）`：进入 **图文发布流程（流程 A.1）**。
4. 用户只提供网页 URL：先用 WebFetch 提取内容和图片，再给出可发布草稿等待确认。
5. 信息不全：先补齐缺失信息，不要直接发布。

## 必做约束

- **发布前必须让用户确认最终标题、正文和图片/视频**。
- **推荐使用分步发布**：先 fill → 用户确认 → 再 click-publish。
- 图文发布时，没有图片不得发布。
- 视频发布时，没有视频不得发布。图片和视频不可混合（二选一）。
- 标题长度不超过 20（UTF-16 编码计算，中文字符计 1，英文/数字/空格计 1）。
- 如果使用文件路径，必须使用绝对路径，禁止相对路径。
- 需要先有运行中的 Chrome，且已登录。

## 流程 A: 图文/视频发布

### Step A.1: 处理内容

#### 完整内容模式
直接使用用户提供的标题和正文。

#### URL 提取模式
1. 使用 WebFetch 提取网页内容。
2. 提取关键信息：标题、正文、图片 URL。
3. 适当总结内容，保持语言自然、适合小红书阅读习惯。
4. 如果提取不到图片，告知用户手动获取。

### Step A.2: 内容检查

#### 标题检查
标题长度必须 ≤ 20（UTF-16 编码长度）。如果超长，自动生成符合长度的新标题。

#### 正文格式
- 段落之间使用双换行分隔。
- 简体中文，语言自然。
- 话题标签放在正文最后一行，格式：`#标签1 #标签2 #标签3`

### Step A.3: 用户确认

通过 `AskUserQuestion` 展示即将发布的内容（标题、正文、图片/视频），获得明确确认后继续。

### Step A.4: 写入临时文件

将标题和正文写入 UTF-8 文本文件。不要在命令行参数中内联中文文本。

### Step A.5: 执行发布（推荐分步方式）

#### 分步发布（推荐）

先填写表单，让用户在浏览器中确认预览后再发布：

```bash
# 步骤 1: 填写图文表单（不发布）
python scripts/cli.py fill-publish \
  --title-file /tmp/xhs_title.txt \
  --content-file /tmp/xhs_content.txt \
  --images "/abs/path/pic1.jpg" "/abs/path/pic2.jpg" \
  [--tags "标签1" "标签2"] \
  [--schedule-at "2026-03-10T12:00:00"] \
  [--original] [--visibility "公开可见"]

# 步骤 2: 通过 AskUserQuestion 让用户确认浏览器中的预览

# 步骤 3: 点击发布
python scripts/cli.py click-publish
```

视频分步发布：

```bash
# 步骤 1: 填写视频表单（不发布）
python scripts/cli.py fill-publish-video \
  --title-file /tmp/xhs_title.txt \
  --content-file /tmp/xhs_content.txt \
  --video "/abs/path/video.mp4" \
  [--tags "标签1" "标签2"] \
  [--visibility "公开可见"]

# 步骤 2: 用户确认

# 步骤 3: 点击发布
python scripts/cli.py click-publish
```

#### 一步到位发布（快捷方式）

```bash
# 图文一步到位
python scripts/cli.py publish \
  --title-file /tmp/xhs_title.txt \
  --content-file /tmp/xhs_content.txt \
  --images "/abs/path/pic1.jpg" "/abs/path/pic2.jpg"

# 视频一步到位
python scripts/cli.py publish-video \
  --title-file /tmp/xhs_title.txt \
  --content-file /tmp/xhs_content.txt \
  --video "/abs/path/video.mp4"

# 带标签和定时发布
python scripts/cli.py publish \
  --title-file /tmp/xhs_title.txt \
  --content-file /tmp/xhs_content.txt \
  --images "/abs/path/pic1.jpg" \
  --tags "标签1" "标签2" \
  --schedule-at "2026-03-10T12:00:00" \
  --original
```

#### Headless 模式（无头自动降级）

```bash
# 使用 --headless 参数，未登录时自动切换到有窗口模式
python scripts/cli.py publish --headless \
  --title-file /tmp/xhs_title.txt \
  --content-file /tmp/xhs_content.txt \
  --images "/abs/path/pic1.jpg"

# 发布流水线（含图片下载和登录检查 + 自动降级）
python scripts/publish_pipeline.py --headless \
  --title-file /tmp/xhs_title.txt \
  --content-file /tmp/xhs_content.txt \
  --images "https://example.com/pic1.jpg" "/abs/path/pic2.jpg"
```

当 `--headless` + 未登录时，脚本会：
1. 关闭无头 Chrome
2. 以有窗口模式重新启动 Chrome
3. 返回 JSON 包含 `"action": "switched_to_headed"`
4. 提示用户在浏览器中扫码登录

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

## 流程 B: 长文发布

当用户说"发长文 / 写长文 / 长文模式"时触发。长文模式使用小红书的长文编辑器，支持排版模板。

### Step B.1: 准备长文内容

收集标题和正文。长文标题使用 textarea 输入，没有 20 字限制（但建议简洁）。

### Step B.2: 用户确认标题和正文

通过 `AskUserQuestion` 确认长文内容。

### Step B.3: 写入临时文件并执行长文模式

```bash
python scripts/cli.py long-article \
  --title-file /tmp/xhs_title.txt \
  --content-file /tmp/xhs_content.txt \
  [--images "/abs/path/pic1.jpg" "/abs/path/pic2.jpg"]
```

该命令会：
1. 导航到发布页
2. 点击"写长文" tab
3. 点击"新的创作"
4. 填写标题和正文
5. 点击"一键排版"
6. 返回 JSON 包含 `templates` 列表

### Step B.4: 选择排版模板

通过 `AskUserQuestion` 展示可用模板列表，让用户选择：

```bash
python scripts/cli.py select-template --name "用户选择的模板名"
```

### Step B.5: 进入发布页

```bash
# 点击下一步，填写发布页描述（正文摘要，不超过 1000 字）
python scripts/cli.py next-step \
  --content-file /tmp/xhs_description.txt
```

注意：发布页的描述编辑器是独立的，需要单独填入内容。如果描述超过 1000 字，脚本会自动截断到 800 字。

### Step B.6: 用户确认并发布

```bash
# 用户在浏览器中确认预览后
python scripts/cli.py click-publish
```

## 处理输出

- **Exit code 0**：成功。输出 JSON 包含 `success`, `title`, `images`/`video`/`templates`, `status`。
- **Exit code 1**：未登录，提示用户先登录（参考 xhs-auth）。若使用 `--headless` 且自动降级，JSON 中 `action` 为 `switched_to_headed`。
- **Exit code 2**：错误，报告 JSON 中的 `error` 字段。

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
| `--headless` | 无头模式（未登录自动降级到有窗口模式） |
| `--host HOST` | 远程 CDP 主机 |
| `--port PORT` | CDP 端口（默认 9222） |
| `--account name` | 指定账号 |

## 失败处理

- **登录失败**：提示用户重新扫码登录并重试。使用 `--headless` 时会自动降级到有窗口模式。
- **图片下载失败**：提示更换图片 URL 或改用本地图片。
- **视频处理超时**：视频上传后需等待处理（最长 10 分钟），超时后提示重试。
- **标题过长**：自动缩短标题，保持语义。
- **页面选择器失效**：提示检查脚本中的选择器定义。
- **模板加载超时**：长文模式下模板可能加载缓慢，等待 15 秒后超时。
