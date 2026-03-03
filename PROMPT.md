# 小红书 Skills 开发任务

## 目标

基于 xiaohongshu-mcp Go 源码，从零重写 Python CDP 引擎，为 OpenClaw 生态构建完整的小红书自动化 Skills。

## 参考资料

- **xiaohongshu-mcp Go 源码**: `/Users/zy/src/zy/xiaohongshu-mcp/` — 10k stars，13 个 MCP 工具
- **xiaohongshu-mcp 数据结构**: `/Users/zy/src/zy/xiaohongshu-mcp/xiaohongshu/types.go`
- **xiaohongshu-mcp 工具定义**: `/Users/zy/src/zy/xiaohongshu-mcp/mcp_server.go`

## 架构

### 模块结构

```
scripts/
├── xhs/                        # 核心 XHS 自动化包
│   ├── cdp.py                  # CDP WebSocket 客户端
│   ├── stealth.py              # 反检测 JS 注入 + Chrome 启动参数
│   ├── cookies.py              # Cookie 文件持久化
│   ├── types.py                # 数据类型（dataclass）
│   ├── errors.py               # 异常体系
│   ├── selectors.py            # CSS 选择器常量
│   ├── urls.py                 # URL 常量
│   ├── human.py                # 人类行为模拟
│   ├── login.py                # 登录
│   ├── feeds.py                # 首页 Feed
│   ├── search.py               # 搜索 + 筛选
│   ├── feed_detail.py          # 笔记详情 + 评论加载
│   ├── user_profile.py         # 用户主页
│   ├── comment.py              # 评论、回复
│   ├── like_favorite.py        # 点赞、收藏
│   ├── publish.py              # 图文发布
│   └── publish_video.py        # 视频发布
├── cli.py                      # 统一 CLI 入口（13 个子命令）
├── chrome_launcher.py          # Chrome 进程管理
├── account_manager.py          # 多账号管理
├── image_downloader.py         # 媒体下载（SHA256 缓存）
├── title_utils.py              # UTF-16 标题长度计算
├── run_lock.py                 # 单实例锁
└── publish_pipeline.py         # 发布编排器
```

### CLI 接口（对应 Go 的 13 个 MCP 工具）

```bash
python scripts/cli.py check-login
python scripts/cli.py login
python scripts/cli.py delete-cookies
python scripts/cli.py list-feeds
python scripts/cli.py search-feeds --keyword "关键词" [--sort-by --note-type ...]
python scripts/cli.py get-feed-detail --feed-id ID --xsec-token TOKEN [--load-all-comments]
python scripts/cli.py user-profile --user-id ID --xsec-token TOKEN
python scripts/cli.py post-comment --feed-id ID --xsec-token TOKEN --content "内容"
python scripts/cli.py reply-comment --feed-id ID --xsec-token TOKEN --content "内容" [--comment-id | --user-id]
python scripts/cli.py like-feed --feed-id ID --xsec-token TOKEN [--unlike]
python scripts/cli.py favorite-feed --feed-id ID --xsec-token TOKEN [--unfavorite]
python scripts/cli.py publish --title-file T --content-file C --images P1 P2 [--tags --schedule-at --visibility]
python scripts/cli.py publish-video --title-file T --content-file C --video P [--tags --schedule-at]
```

全局选项：`--host`, `--port`, `--account`
输出：JSON（`ensure_ascii=False`）
退出码：0=成功，1=未登录，2=错误

## 代码规范要求

- Python 代码必须通过 `ruff check` 和 `ruff format`
- 完整的 type hints（PEP 484），使用 `str | None` 而非 `Optional[str]`
- 公共函数和类必须有 docstring
- 行长度上限 100 字符
- 使用 `from __future__ import annotations` 启用延迟注解
- 异常类统一继承自 `XHSError`
- CLI 使用 argparse，exit code: 0=成功，1=未登录，2=错误
- JSON 输出使用 `ensure_ascii=False` 保留中文

## 完成标志

当以下条件全部满足时，输出完成标志：
1. `xhs/` 包 17 个模块已全部创建
2. `cli.py` 13 个子命令已实现
3. 5 个支撑脚本已重写
4. 5 个 `skills/*/SKILL.md` 已更新
5. 根目录 `SKILL.md`、`CLAUDE.md`、`README.md` 已更新
6. `uv run ruff check .` 无错误
7. `uv run ruff format --check .` 无差异

<promise>ALL SKILLS COMPLETE</promise>
