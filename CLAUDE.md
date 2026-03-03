# xiaohongshu-skills

小红书自动化 Claude Code Skills，基于 Python CDP 浏览器自动化引擎。
为 OpenClaw 生态提供小红书操作能力，同时支持 Claude Code skills 格式。

## 项目结构

```
xiaohongshu-skills/
├── scripts/                        # Python CDP 自动化引擎
│   ├── xhs/                        # 核心 XHS 自动化包
│   │   ├── __init__.py
│   │   ├── cdp.py                  # CDP WebSocket 客户端（Browser, Page, Element）
│   │   ├── stealth.py              # 反检测 JS 注入 + Chrome 启动参数
│   │   ├── cookies.py              # Cookie 文件持久化
│   │   ├── types.py                # 数据类型（dataclass）
│   │   ├── errors.py               # 异常体系
│   │   ├── selectors.py            # CSS 选择器常量
│   │   ├── urls.py                 # URL 常量和构建函数
│   │   ├── human.py                # 人类行为模拟（延迟、滚动）
│   │   ├── login.py                # 登录检查、二维码登录
│   │   ├── feeds.py                # 首页 Feed 列表
│   │   ├── search.py               # 搜索 + 筛选
│   │   ├── feed_detail.py          # 笔记详情 + 评论加载
│   │   ├── user_profile.py         # 用户主页
│   │   ├── comment.py              # 评论、回复
│   │   ├── like_favorite.py        # 点赞、收藏
│   │   ├── publish.py              # 图文发布
│   │   └── publish_video.py        # 视频发布
│   ├── cli.py                      # 统一 CLI 入口（13 个子命令）
│   ├── chrome_launcher.py          # Chrome 进程管理
│   ├── account_manager.py          # 多账号管理
│   ├── image_downloader.py         # 媒体下载（SHA256 缓存）
│   ├── title_utils.py              # UTF-16 标题长度计算
│   ├── run_lock.py                 # 单实例锁
│   └── publish_pipeline.py         # 发布编排器
├── skills/                         # Claude Code Skills 定义
│   ├── xhs-auth/SKILL.md           # 认证管理
│   ├── xhs-publish/SKILL.md        # 内容发布（图文+视频）
│   ├── xhs-explore/SKILL.md        # 内容发现与分析
│   ├── xhs-interact/SKILL.md       # 社交互动（评论/点赞/收藏）
│   └── xhs-content-ops/SKILL.md    # 复合内容运营工作流
├── pyproject.toml                  # uv 项目配置
├── SKILL.md                        # 统一入口（路由到子技能）
├── CLAUDE.md                       # 本文件
├── PROMPT.md                       # Ralph Loop 驱动文件
└── README.md
```

## 技术栈

- **Python**: >=3.11
- **包管理**: uv
- **依赖**: requests + websockets（直接 CDP WebSocket 通信）
- **浏览器**: Chrome（通过 CDP 远程调试协议控制）
- **代码规范**: ruff（lint + format）
- **数据提取**: `window.__INITIAL_STATE__`（与 Go 源码一致）

## 开发命令

```bash
uv sync                    # 安装依赖
uv run ruff check .        # Lint 检查
uv run ruff format .       # 代码格式化
uv run pytest              # 运行测试
```

## 架构设计

### 双层结构

1. **scripts/ — Python CDP 引擎**
   - 基于 xiaohongshu-mcp Go 源码从零重写
   - `xhs/` 包：模块化的核心自动化库
   - `cli.py`：统一 CLI 入口，13 个子命令对应 MCP 工具
   - JSON 结构化输出，便于 agent 解析
   - 多账号支持，独立 Chrome Profile 隔离
   - 反检测保护（stealth flags + JS 注入）

2. **skills/ — Claude Code Skills 定义**
   - SKILL.md 格式，指导 Claude 如何调用 scripts/
   - 包含输入判断、约束规则、工作流程、失败处理

### 调用方式

```bash
# 统一 CLI 入口
python scripts/cli.py check-login
python scripts/cli.py search-feeds --keyword "关键词"
python scripts/cli.py publish --title-file t.txt --content-file c.txt --images pic.jpg

# 发布流水线（含图片下载和登录检查）
python scripts/publish_pipeline.py --title-file t.txt --content-file c.txt --images URL1
```

## 代码规范

### Python 风格
- 遵循 PEP 8，使用 ruff 强制执行
- 完整的 type hints（PEP 484），使用 `str | None` 语法
- 公共函数和类必须有 docstring
- 行长度上限 100 字符
- 使用 `from __future__ import annotations` 启用延迟注解

### 命名约定
- 文件名：snake_case
- 类名：PascalCase
- 函数/变量：snake_case
- 常量：UPPER_SNAKE_CASE

### 错误处理
- 自定义异常类继承自 `XHSError` 基类（`xhs/errors.py`）
- CLI 命令使用结构化 exit code：0=成功，1=未登录，2=错误
- 所有用户可见的错误信息使用中文

### 安全约束
- 发布类操作必须有用户确认机制
- 文件路径必须使用绝对路径
- 不在命令行参数中内联敏感内容（使用文件传递）
- Chrome Profile 目录隔离账号 cookies

## 参考资源

- **xiaohongshu-mcp Go 源码**: /Users/zy/src/zy/xiaohongshu-mcp/

## MCP 工具对照表

scripts/cli.py 的 13 个子命令对应 xiaohongshu-mcp 的 MCP 工具：

| CLI 子命令 | MCP 工具 | 分类 |
|--|--|--|
| `check-login` | check_login_status | 认证 |
| `login` | get_login_qrcode | 认证 |
| `delete-cookies` | delete_cookies | 认证 |
| `list-feeds` | list_feeds | 浏览 |
| `search-feeds` | search_feeds | 浏览 |
| `get-feed-detail` | get_feed_detail | 浏览 |
| `user-profile` | user_profile | 浏览 |
| `post-comment` | post_comment_to_feed | 互动 |
| `reply-comment` | reply_comment_in_feed | 互动 |
| `like-feed` | like_feed | 互动 |
| `favorite-feed` | favorite_feed | 互动 |
| `publish` | publish_content | 发布 |
| `publish-video` | publish_with_video | 发布 |
