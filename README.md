# xiaohongshu-skills

小红书自动化 Skills，直接使用你已登录的浏览器和真实账号，以普通用户的方式操作小红书。

支持 [OpenClaw](https://github.com/anthropics/openclaw) 及所有兼容 `SKILL.md` 格式的 AI Agent 平台（如 Claude Code）。

> **⚠️ 使用建议**：虽然本项目使用真实的用户浏览器和账号环境，但仍建议**控制使用频率**，避免短时间内大量操作。频繁的自动化行为可能触发小红书的风控机制，导致账号受限。

> 📰 作者也在运营一个 AI 资讯网站 [![LLM大模型邮报 - 中文 AI 资讯快报：大模型动态、工程实践与行业观察](https://img.shields.io/badge/官网-llmposts.com-2563eb?style=flat-square&logo=googlenews&logoColor=white)](https://llmposts.com?utm_source=github&utm_medium=readme&utm_campaign=xiaohongshu-skills&utm_content=readme_intro "LLM大模型邮报 - 中文 AI 资讯快报：大模型动态、工程实践与行业观察") — 欢迎关注。


## 功能概览

| 技能 | 说明 | 核心能力 |
|------|------|----------|
| **xhs-auth** | 认证管理 | 登录检查、扫码登录、手机验证码登录 |
| **xhs-publish** | 内容发布 | 图文 / 视频 / 长文发布、定时发布、分步预览 |
| **xhs-explore** | 内容发现 | 关键词搜索、笔记详情、用户主页、首页推荐 |
| **xhs-interact** | 社交互动 | 评论、回复、点赞、收藏 |
| **xhs-content-ops** | 复合运营 | 竞品分析、热点追踪、批量互动、内容创作 |

支持**连贯操作** — 你可以用自然语言下达复合指令，Agent 会自动串联多个技能完成任务。例如：

> "搜索刺客信条最火的图文帖子，收藏它，然后告诉我讲了什么"

Agent 会自动执行：搜索 → 筛选图文 → 按点赞排序 → 收藏 → 获取详情 → 总结内容。

## 安装

### 前置条件

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) 包管理器
- Google Chrome 浏览器

### 第一步：安装项目

**方法一：下载 ZIP（推荐）**

1. 在 GitHub 仓库页面点击 **Code → Download ZIP**，下载并解压到你的 Agent skills 目录：

```
# OpenClaw 示例
<openclaw-project>/skills/xiaohongshu-skills/

# Claude Code 示例
<your-project>/.claude/skills/xiaohongshu-skills/
```

**方法二：Git Clone**

```bash
cd <your-agent-project>/skills/
git clone https://github.com/autoclaw-cc/xiaohongshu-skills.git
```

2. 安装 Python 依赖：

```bash
cd xiaohongshu-skills
uv sync
```

### 第二步：安装浏览器扩展

扩展让 AI 能够在你的浏览器中以你的身份操作小红书，使用的是你真实的登录状态和账号信息。

1. 打开 Chrome，地址栏输入 `chrome://extensions/`
2. 右上角开启**开发者模式**
3. 点击**加载已解压的扩展程序**，选择本项目的 `extension/` 目录
4. 确认扩展 **XHS Bridge** 已启用

安装完成后即可使用 — 所有操作都发生在你自己的浏览器里，使用你的真实账号和浏览器环境。

## 使用方式

### 作为 AI Agent 技能使用（推荐）

安装到 skills 目录后，直接用自然语言与 Agent 对话即可。Agent 会根据你的意图自动路由到对应技能。

**认证登录：**
> "登录小红书" / "检查登录状态"

**搜索浏览：**
> "搜索关于露营的笔记" / "查看这条笔记的详情"

**发布内容：**
> "帮我发一条图文笔记，标题是…，配图是…"

**社交互动：**
> "给这条笔记点赞" / "收藏这条帖子" / "评论：写得太好了"

**复合操作：**
> "搜索竞品账号最近的爆款笔记，分析他们的选题方向"

### 作为 CLI 工具使用

所有功能也可以通过命令行直接调用，输出 JSON 格式，便于脚本集成。

```bash
# 检查登录状态
python scripts/cli.py check-login

# 扫码登录
python scripts/cli.py login

# 搜索笔记
python scripts/cli.py search-feeds --keyword "关键词"

# 带筛选条件
python scripts/cli.py search-feeds \
  --keyword "关键词" \
  --sort-by "最多点赞" \
  --note-type "图文"

# 查看笔记详情
python scripts/cli.py get-feed-detail \
  --feed-id FEED_ID --xsec-token XSEC_TOKEN

# 图文发布（分步：填写 → 预览 → 确认）
python scripts/cli.py fill-publish \
  --title-file title.txt \
  --content-file content.txt \
  --images "/abs/path/pic1.jpg" "/abs/path/pic2.jpg"
python scripts/cli.py click-publish

# 一步发布图文
python scripts/cli.py publish \
  --title-file title.txt \
  --content-file content.txt \
  --images "/abs/path/pic1.jpg" \
  --tags "标签1" "标签2"

# 视频发布
python scripts/cli.py publish-video \
  --title-file title.txt \
  --content-file content.txt \
  --video "/abs/path/video.mp4"

# 点赞 / 收藏 / 评论
python scripts/cli.py like-feed --feed-id FEED_ID --xsec-token XSEC_TOKEN
python scripts/cli.py favorite-feed --feed-id FEED_ID --xsec-token XSEC_TOKEN
python scripts/cli.py post-comment --feed-id FEED_ID --xsec-token XSEC_TOKEN --content "评论内容"
```

> 第一次运行时，若 Chrome 未打开，CLI 会自动启动它。

## CLI 命令参考

| 子命令 | 说明 |
|--------|------|
| `check-login` | 检查登录状态，返回用户昵称和小红书号 |
| `login` | 获取登录二维码，等待扫码，登录后返回用户信息 |
| `delete-cookies` | 清除 cookies（退出登录） |
| `list-feeds` | 获取首页推荐 Feed |
| `search-feeds` | 关键词搜索笔记（支持排序/类型/时间/范围/位置筛选） |
| `get-feed-detail` | 获取笔记完整内容和评论 |
| `user-profile` | 获取用户主页信息和帖子列表 |
| `post-comment` | 对笔记发表评论 |
| `reply-comment` | 回复指定评论 |
| `like-feed` | 点赞 / 取消点赞 |
| `favorite-feed` | 收藏 / 取消收藏 |
| `publish` | 一步发布图文 |
| `publish-video` | 一步发布视频 |
| `fill-publish` | 填写图文表单（不发布，供预览） |
| `fill-publish-video` | 填写视频表单（不发布，供预览） |
| `click-publish` | 确认发布（点击发布按钮） |
| `save-draft` | 保存为草稿 |
| `long-article` | 长文模式：填写 + 一键排版 |
| `select-template` | 选择长文排版模板 |
| `next-step` | 长文下一步 + 填写描述 |

退出码：`0` 成功 · `1` 未登录 · `2` 错误

## 项目结构

```
xiaohongshu-skills/
├── extension/                      # Chrome 扩展
│   ├── manifest.json
│   ├── background.js
│   └── content.js
├── scripts/                        # Python 自动化引擎
│   ├── xhs/                        # 核心自动化包
│   │   ├── bridge.py               # 扩展通信客户端
│   │   ├── selectors.py            # CSS 选择器（集中管理）
│   │   ├── login.py                # 登录 + 用户信息获取
│   │   ├── feeds.py                # 首页 Feed
│   │   ├── search.py               # 搜索 + 筛选
│   │   ├── feed_detail.py          # 笔记详情 + 评论加载
│   │   ├── user_profile.py         # 用户主页
│   │   ├── comment.py              # 评论、回复
│   │   ├── like_favorite.py        # 点赞、收藏
│   │   ├── publish.py              # 图文发布
│   │   ├── publish_video.py        # 视频发布
│   │   ├── publish_long_article.py # 长文发布
│   │   ├── types.py                # 数据类型
│   │   ├── errors.py               # 异常体系
│   │   ├── urls.py                 # URL 常量
│   │   ├── cookies.py              # Cookie 持久化
│   │   └── human.py                # 行为模拟
│   ├── cli.py                      # 统一 CLI 入口
│   ├── bridge_server.py            # 本地通信服务
│   ├── image_downloader.py         # 媒体下载（SHA256 缓存）
│   ├── title_utils.py              # UTF-16 标题长度计算
│   └── run_lock.py                 # 单实例锁
├── skills/                         # Claude Code Skills 定义
│   ├── xhs-auth/SKILL.md
│   ├── xhs-publish/SKILL.md
│   ├── xhs-explore/SKILL.md
│   ├── xhs-interact/SKILL.md
│   └── xhs-content-ops/SKILL.md
├── SKILL.md                        # 技能统一入口（路由到子技能）
├── CLAUDE.md                       # 项目开发指南
├── pyproject.toml
└── README.md
```

## 开发

```bash
uv sync                    # 安装依赖
uv run ruff check .        # Lint 检查
uv run ruff format .       # 代码格式化
uv run pytest              # 运行测试
```

## License

MIT

## Star History

[![Star History Chart](https://api.star-history.com/image?repos=autoclaw-cc/xiaohongshu-skills&type=date&legend=top-left)](https://www.star-history.com/?repos=autoclaw-cc%2Fxiaohongshu-skills&type=date&legend=top-left)
