# 小红书 Skills P0 增强任务

## 目标

在现有 13 个 MCP 工具基础上，补充 3 项 P0 能力：写长文发布模式、Headless 自动降级、分步 CLI 命令。

## 参考资料

- **xiaohongshu-mcp/skills Python 实现**（主要参考）:
  - `/Users/zy/src/zy/xiaohongshu-mcp/skills/post-to-xhs/scripts/cdp_publish.py` — 长文发布核心逻辑
  - `/Users/zy/src/zy/xiaohongshu-mcp/skills/post-to-xhs/scripts/publish_pipeline.py` — headless 降级逻辑
  - `/Users/zy/src/zy/xiaohongshu-mcp/skills/post-to-xhs/scripts/chrome_launcher.py` — Chrome 重启/模式切换
  - `/Users/zy/src/zy/xiaohongshu-mcp/skills/post-to-xhs/SKILL.md` — 长文工作流 SKILL 定义
  - `/Users/zy/src/zy/xiaohongshu-mcp/skills/post-to-xhs/references/publish-workflow.md` — DOM 选择器参考

- **当前项目代码**: `/Users/zy/src/zy/00_autoclaw/xiaohongshu-skills/scripts/`

## 代码规范

- `uv run ruff check .` 无错误
- `uv run ruff format --check .` 无差异
- 完整 type hints，`from __future__ import annotations`
- 公共函数有 docstring
- 行长度 ≤ 100
- 异常继承 `XHSError`
- JSON 输出 `ensure_ascii=False`
- Exit code: 0=成功，1=未登录，2=错误

## 任务拆解

### Task 1: 写长文发布模式

参考 `cdp_publish.py` 的 `publish_long_article()`、`get_template_names()`、`select_template()`、`click_next_and_prepare_publish()` 方法。

#### 1.1 新增选择器（`xhs/selectors.py`）

添加长文模式相关的 CSS 选择器：
- `LONG_ARTICLE_TAB` — "写长文" tab
- `NEW_CREATION_BUTTON` — "新的创作" 按钮
- `LONG_ARTICLE_TITLE` — 长文标题 textarea
- `AUTO_FORMAT_BUTTON` — "一键排版" 按钮
- `TEMPLATE_CARD` — 模板卡片
- `TEMPLATE_TITLE` — 模板名称
- `NEXT_STEP_BUTTON` — "下一步" 按钮

参考 reference `publish-workflow.md` 中的 DOM 选择器参考表。

#### 1.2 新增长文发布模块（`xhs/publish_long_article.py`）

创建独立模块，包含以下函数：

```python
def publish_long_article(page, title, content, image_paths=None) -> list[str]:
    """长文发布：导航 → 点击写长文 → 新的创作 → 填写标题正文 → 一键排版。
    返回可用模板名称列表。"""

def get_template_names(page) -> list[str]:
    """获取当前可用的排版模板名称列表。"""

def select_template(page, template_name) -> bool:
    """选择指定名称的排版模板。"""

def click_next_and_fill_description(page, description) -> None:
    """点击下一步，进入发布页并填写正文描述。
    注意：发布页有独立的正文编辑器，需单独填入。
    如果 description 超过 1000 字，应压缩到 800 字左右。"""
```

#### 1.3 新增 CLI 子命令（`cli.py`）

添加 3 个子命令：

```bash
# 长文模式：填写内容 + 一键排版，返回模板列表
python scripts/cli.py long-article \
  --title-file T --content-file C [--images P1 P2]

# 选择模板
python scripts/cli.py select-template --name "模板名"

# 点击下一步 + 填写发布页描述
python scripts/cli.py next-step --content-file C
```

#### 1.4 更新 SKILL.md

在 `skills/xhs-publish/SKILL.md` 中添加写长文模式的完整工作流：
- 输入判断：用户说"发长文 / 写长文 / 长文模式"时触发
- Step B.1-B.5 的工作流
- 模板选择通过 AskUserQuestion 让用户选

### Task 2: Headless 自动降级

参考 `publish_pipeline.py` 的登录检查 + 模式切换逻辑，以及 `chrome_launcher.py` 的 `restart_chrome()`。

#### 2.1 增强 `chrome_launcher.py`

添加 `restart_chrome()` 函数：
- 关闭当前 Chrome 实例
- 以新模式（headless 或 headed）重新启动
- 等待端口就绪

#### 2.2 增强 `publish_pipeline.py`

在 `run_publish_pipeline()` 中加入降级逻辑：

```
检查登录 → 如果未登录且是 headless 模式：
  1. 关闭无头 Chrome
  2. 以有窗口模式重新启动 Chrome
  3. 打开登录页
  4. 返回 {"success": false, "error": "未登录", "action": "switched_to_headed", "message": "已切换到有窗口模式，请在浏览器中扫码登录"}
  5. exit code 1
```

#### 2.3 新增 CLI 参数

给 `publish` 和 `publish-video` 子命令添加 `--headless` 参数：

```bash
python scripts/cli.py publish --headless \
  --title-file T --content-file C --images P1 P2
```

当 `--headless` + 未登录时，自动降级到有窗口模式。

### Task 3: 分步 CLI 命令

参考 `cdp_publish.py` 的 `fill`、`click-publish` 子命令设计。目标是让 agent 可以在填写表单和点击发布之间插入用户确认步骤。

#### 3.1 新增 CLI 子命令

```bash
# 只填写表单，不发布（图文模式）
python scripts/cli.py fill-publish \
  --title-file T --content-file C --images P1 P2 \
  [--tags --schedule-at --visibility --original]

# 只填写表单，不发布（视频模式）
python scripts/cli.py fill-publish-video \
  --title-file T --content-file C --video P \
  [--tags --schedule-at --visibility]

# 点击发布按钮（在用户确认后调用）
python scripts/cli.py click-publish
```

#### 3.2 拆分现有 publish 逻辑

在 `xhs/publish.py` 中将 `publish_image_content()` 拆分为：
- `fill_publish_form(page, content)` — 导航、上传、填写表单，**不点击发布**
- `click_publish_button(page)` — 仅点击发布按钮

`publish_image_content()` 保持不变（内部调用两者），向后兼容。

同理拆分 `xhs/publish_video.py`。

#### 3.3 更新 SKILL.md

在 `skills/xhs-publish/SKILL.md` 中：
- 推荐的发布流程改为：fill → 用户通过 AskUserQuestion 确认 → click-publish
- 保留一步到位的 `publish` 命令作为快捷方式

### Task 4: 验证 + 收尾

- `uv run ruff check .` 无错误
- `uv run ruff format --check .` 无差异
- 所有新增 CLI 子命令 `--help` 正常输出
- `skills/xhs-publish/SKILL.md` 包含长文模式和分步发布的完整工作流
- `CLAUDE.md` 的 MCP 工具对照表更新（新增子命令）

## 完成标志

当以下条件全部满足时，输出完成标志：
1. `xhs/publish_long_article.py` 已创建，含 4 个核心函数
2. `cli.py` 新增 6 个子命令：`long-article`, `select-template`, `next-step`, `fill-publish`, `fill-publish-video`, `click-publish`
3. `chrome_launcher.py` 含 `restart_chrome()` 函数
4. `publish_pipeline.py` 含 headless 自动降级逻辑
5. `skills/xhs-publish/SKILL.md` 含长文模式和分步发布工作流
6. `uv run ruff check .` 无错误
7. `uv run ruff format --check .` 无差异

<promise>P0 ENHANCE COMPLETE</promise>
