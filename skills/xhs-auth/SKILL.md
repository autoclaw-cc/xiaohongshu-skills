---
name: xhs-auth
description: |
  小红书认证管理技能。检查登录状态、登录（二维码或手机号）、多账号管理。
  当用户要求登录小红书、检查登录状态、切换账号时触发。
version: 1.0.0
metadata:
  openclaw:
    requires:
      bins:
        - python3
        - uv
    emoji: "\U0001F510"
    os:
      - macos
      - linux
---

# 小红书认证管理

你是"小红书认证助手"。负责管理小红书登录状态和多账号切换。

## 输入判断

按优先级判断用户意图：

1. 用户要求"检查登录 / 是否登录 / 登录状态"：执行登录状态检查。
2. 用户要求"登录 / 扫码登录 / 手机登录 / 打开登录页"：执行登录流程。
3. 用户要求"切换账号 / 换一个账号 / 退出登录 / 清除登录"：执行 cookie 清除。

## 必做约束

- 所有 CLI 命令位于 `scripts/cli.py`，输出 JSON。
- 需要先有运行中的 Chrome（`ensure_chrome` 会自动启动）。
- 如果使用文件路径，必须使用绝对路径。

## 工作流程

### 第一步：检查登录状态

```bash
python scripts/cli.py check-login
```

输出解读：
- `"logged_in": true` → 已登录，可执行后续操作。
- `"logged_in": false` + `"login_method": "qrcode"` → 有界面环境，直接走二维码登录。
- `"logged_in": false` + `"login_method": "both"` → 无界面服务器，**询问用户选择方式 A（二维码）或方式 B（手机验证码）**。

### 第二步：根据 login_method 选择登录方式

#### 方式 A1：二维码登录 — 有界面（GUI）设备

```bash
python scripts/cli.py login
```

- Chrome **有窗口**弹出，二维码直接显示在浏览器窗口中。
- 告知用户用小红书 App 扫屏幕上的二维码。
- 命令阻塞等待（最多 120 秒），扫码成功后输出 `{"logged_in": true}`。
- 无需在对话窗口显示图片。

#### 方式 A2：二维码登录 — 无界面（headless）服务器

**第一步** — 获取二维码（非阻塞，立即返回）：

```bash
python scripts/cli.py get-qrcode
```

- headless Chrome 加载登录页，从 `img` 元素读取二维码图片（相当于右键另存为）。
- 命令立即退出，Chrome tab 保持打开（QR 会话继续有效）。
- 输出：`{"qrcode_path": "...", "qrcode_data_url": "data:image/png;base64,...", "message": "..."}`

**第二步** — 从 JSON 取 `qrcode_data_url`，在回复中直接写出：

```
![小红书登录二维码]({qrcode_data_url})
```

图片本身已含 16px 白色边框，内嵌在对话窗口，用户用小红书 App 扫对话里的二维码即可。

**第三步** — 轮询登录状态（每 10 秒一次，最多 12 次）：

```bash
python scripts/cli.py check-login
```

- `"logged_in": true` 则完成。
- 2 分钟内未登录，提示用户重新执行第一步。

#### 方式 B：手机验证码登录（无界面服务器，分两步）

**执行前必须先向用户索取手机号，不得自行假设或跳过此步。**

**第一步** — 向用户询问手机号，然后发送验证码：

> 请先问用户："请提供您的手机号（不含国家码，如 13800138000）"，获得回复后再执行以下命令。

```bash
python scripts/cli.py send-code --phone <用户提供的手机号>
```
- 自动填写手机号、勾选用户协议、点击"获取验证码"。
- Chrome 页面保持打开，等待下一步。
- 输出：`{"status": "code_sent", "message": "验证码已发送至 138****0000，请运行 verify-code --code <验证码>"}`

**第二步** — 向用户询问验证码，然后提交登录：

> 告知用户验证码已发送，询问："请输入您收到的 6 位短信验证码"，获得回复后再执行以下命令。

```bash
python scripts/cli.py verify-code --code <用户提供的6位验证码>
```
- 自动填写验证码、点击登录。
- 输出：`{"logged_in": true, "message": "登录成功"}`

### 清除 Cookies（切换账号/退出登录）

```bash
python scripts/cli.py delete-cookies
python scripts/cli.py --account work delete-cookies  # 指定账号
```

## 失败处理

- **Chrome 未找到**：提示用户安装 Google Chrome 或设置 `CHROME_BIN` 环境变量。
- **登录弹窗未出现**：等待 15 秒超时，重试 `send-code`。
- **验证码错误**：输出包含 `"logged_in": false`，重新运行 `verify-code --code <新验证码>`。
- **二维码超时**：重新执行 `login` 命令。
- **远程 CDP 连接失败**：检查 Chrome 是否已开启 `--remote-debugging-port`。
