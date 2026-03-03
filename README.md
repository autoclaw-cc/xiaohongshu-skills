# xiaohongshu-skills

小红书自动化 Claude Code Skills，基于 Python CDP 浏览器自动化引擎。

为 OpenClaw 生态提供小红书操作能力，同时兼容 Claude Code Skills 格式。

## 功能概览

| 技能 | 说明 | 核心命令 |
|------|------|----------|
| **xhs-auth** | 认证管理 | `check-login`, `login`, `delete-cookies` |
| **xhs-publish** | 内容发布 | `publish`, `publish-video` |
| **xhs-explore** | 内容发现 | `list-feeds`, `search-feeds`, `get-feed-detail`, `user-profile` |
| **xhs-interact** | 社交互动 | `post-comment`, `reply-comment`, `like-feed`, `favorite-feed` |
| **xhs-content-ops** | 复合运营 | 竞品分析、热点追踪、内容创作、互动管理 |

## 安装

```bash
# 克隆项目
git clone https://github.com/autoclaw-cc/xiaohongshu-skills.git
cd xiaohongshu-skills

# 安装依赖（需要 uv）
uv sync
```

### 前置条件

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) 包管理器
- Google Chrome 浏览器

## 快速开始

### 1. 启动 Chrome

```bash
# 有窗口模式（推荐首次登录）
python scripts/chrome_launcher.py

# 无头模式
python scripts/chrome_launcher.py --headless
```

### 2. 登录小红书

```bash
# 检查登录状态
python scripts/cli.py check-login

# 登录（扫码）
python scripts/cli.py login
```

### 3. 搜索笔记

```bash
python scripts/cli.py search-feeds --keyword "关键词"

# 带筛选
python scripts/cli.py search-feeds \
  --keyword "关键词" --sort-by 最新 --note-type 图文
```

### 4. 查看笔记详情

```bash
python scripts/cli.py get-feed-detail \
  --feed-id FEED_ID --xsec-token XSEC_TOKEN
```

### 5. 发布内容

```bash
# 图文发布
python scripts/cli.py publish \
  --title-file title.txt \
  --content-file content.txt \
  --images "/abs/path/pic1.jpg" "/abs/path/pic2.jpg"

# 视频发布
python scripts/cli.py publish-video \
  --title-file title.txt \
  --content-file content.txt \
  --video "/abs/path/video.mp4"
```

### 6. 社交互动

```bash
# 发表评论
python scripts/cli.py post-comment \
  --feed-id FEED_ID \
  --xsec-token XSEC_TOKEN \
  --content "评论内容"

# 点赞
python scripts/cli.py like-feed \
  --feed-id FEED_ID --xsec-token XSEC_TOKEN

# 收藏
python scripts/cli.py favorite-feed \
  --feed-id FEED_ID --xsec-token XSEC_TOKEN
```

## CLI 命令参考

所有命令通过 `scripts/cli.py` 统一入口调用，输出 JSON 格式。

全局选项：
- `--host HOST` — Chrome 调试主机（默认 127.0.0.1）
- `--port PORT` — Chrome 调试端口（默认 9222）
- `--account NAME` — 指定账号

| 子命令 | 说明 |
|--------|------|
| `check-login` | 检查登录状态 |
| `login` | 获取登录二维码，等待扫码 |
| `delete-cookies` | 清除 cookies |
| `list-feeds` | 获取首页推荐 Feed |
| `search-feeds` | 关键词搜索笔记 |
| `get-feed-detail` | 获取笔记详情和评论 |
| `user-profile` | 获取用户主页信息 |
| `post-comment` | 对笔记发表评论 |
| `reply-comment` | 回复指定评论 |
| `like-feed` | 点赞 / 取消点赞 |
| `favorite-feed` | 收藏 / 取消收藏 |
| `publish` | 发布图文内容 |
| `publish-video` | 发布视频内容 |

退出码：0=成功，1=未登录，2=错误

## 项目结构

```
xiaohongshu-skills/
├── scripts/                        # Python CDP 自动化引擎
│   ├── xhs/                        # 核心自动化包（模块化）
│   │   ├── cdp.py                  # CDP WebSocket 客户端
│   │   ├── stealth.py              # 反检测保护
│   │   ├── cookies.py              # Cookie 持久化
│   │   ├── types.py                # 数据类型
│   │   ├── errors.py               # 异常体系
│   │   ├── selectors.py            # CSS 选择器
│   │   ├── urls.py                 # URL 常量
│   │   ├── human.py                # 人类行为模拟
│   │   ├── login.py                # 登录
│   │   ├── feeds.py                # 首页 Feed
│   │   ├── search.py               # 搜索
│   │   ├── feed_detail.py          # 笔记详情
│   │   ├── user_profile.py         # 用户主页
│   │   ├── comment.py              # 评论
│   │   ├── like_favorite.py        # 点赞/收藏
│   │   ├── publish.py              # 图文发布
│   │   └── publish_video.py        # 视频发布
│   ├── cli.py                      # 统一 CLI（13 个子命令）
│   ├── chrome_launcher.py          # Chrome 进程管理
│   ├── account_manager.py          # 多账号管理
│   ├── image_downloader.py         # 媒体下载
│   ├── title_utils.py              # 标题长度计算
│   ├── run_lock.py                 # 单实例锁
│   └── publish_pipeline.py         # 发布编排器
├── skills/                         # Claude Code Skills 定义
│   ├── xhs-auth/SKILL.md           # 认证管理
│   ├── xhs-publish/SKILL.md        # 内容发布
│   ├── xhs-explore/SKILL.md        # 内容发现
│   ├── xhs-interact/SKILL.md       # 社交互动
│   └── xhs-content-ops/SKILL.md    # 复合运营
├── SKILL.md                        # 统一入口
├── CLAUDE.md                       # 项目开发指南
├── pyproject.toml                  # uv 项目配置
└── README.md
```

## 技术架构

### 双层结构

1. **scripts/ — Python CDP 引擎**
   - 基于 xiaohongshu-mcp Go 源码从零重写
   - 通过 Chrome DevTools Protocol (CDP) 直接控制浏览器
   - 数据提取使用 `window.__INITIAL_STATE__` 模式
   - 内置反检测保护（stealth flags + JS 注入）
   - JSON 结构化输出

2. **skills/ — Claude Code Skills 定义**
   - SKILL.md 格式，指导 AI agent 如何调用 scripts/
   - 包含输入判断、约束规则、工作流程、失败处理

## 开发

```bash
uv sync                    # 安装依赖
uv run ruff check .        # Lint 检查
uv run ruff format .       # 代码格式化
uv run pytest              # 运行测试
```
