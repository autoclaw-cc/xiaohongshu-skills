---
name: xhs-auto-marketing
description: |
  小红书全自动营销技能。搜索高互动帖子 → 自动点赞 → AI生成评论 → 自动发送。
  当用户要求自动化营销、自动评论推广、批量互动宣发、定时营销任务时触发。
  ⚠️ 全自动评论有封号风险，使用者需明确接受。
version: 1.1.0
metadata:
  openclaw:
    requires:
      bins:
        - python3
        - uv
    emoji: "📢"
    os:
      - darwin
      - linux
---

# 小红书全自动营销

自动化营销流程：搜索高互动帖子 → 点赞 → AI生成口语化评论（融入宣发信息）→ 自动发送。

## 🔒 技能边界（强制）

**所有操作通过 `python scripts/auto_marketing.py` 完成。**

## ⚠️ 风险声明

全自动评论有较高封号风险。本技能已内置以下保护机制，但不能完全避免：

- 每日评论上限（默认 15 条）
- 评论间隔 3-8 分钟随机
- 混入纯互动评论（推广:互动 ≈ 7:3）
- 活跃时间窗口（北京时间 8:00-20:00）
- 连续失败 3 次触发熔断（暂停 24 小时）
- 已评论帖子去重

## 账号选择（前置步骤）

```bash
python scripts/cli.py list-accounts
```

根据返回结果选择账号，后续命令加 `--account <名称>`。

## 命令用法

```bash
DISPLAY=:99 python scripts/auto_marketing.py \
  --keywords "AI创业,AI产品,人工智能应用" \
  --filter "AI,人工智能,创业,产品,应用,工具" \
  --promo-info "aifunding是一个AI创业融资平台" \
  --max-notes 8 \
  --max-per-keyword 3 \
  --daily-limit 15 \
  --promo-ratio 0.7 \
  [--account ACCOUNT_NAME] \
  [--dry-run]
```

### 参数说明

| 参数 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `--keywords` | ✅ | - | 搜索关键词，逗号分隔 |
| `--filter` | ❌ | "" | 相关性筛选词，逗号分隔 |
| `--promo-info` | ✅ | - | 宣发信息描述 |
| `--max-notes` | ❌ | 8 | 本轮最多处理笔记数 |
| `--max-per-keyword` | ❌ | 3 | 每个关键词最多处理数 |
| `--daily-limit` | ❌ | 15 | 每日评论上限 |
| `--promo-ratio` | ❌ | 0.7 | 推广评论占比(0-1) |
| `--proxy` | ❌ | "" | 代理地址(socks5://或http://) |
| `--dry-run` | ❌ | false | 试运行，不实际发送评论 |

### 关键词智能泛化

执行前 AI 应对用户提供的关键词进行泛化：

1. **直接关键词**：用户原话中的核心词
2. **场景关键词**：该话题常出现的具体场景
3. **话术关键词**：常用标题表述
4. **标签关键词**：平台热门标签/黑话

同时生成 `--filter` 筛选词（10-25 个）。

## 执行流程

1. 检查熔断器状态
2. 检查时间窗口（北京时间 8:00-20:00）
3. 检查每日剩余配额
4. 搜索关键词 → 筛选相关卡片
5. 逐个打开笔记：
   - 点赞
   - 模拟阅读（3-6秒）
   - AI 生成评论（推广/互动按比例）
   - 发送评论
   - 等待 3-8 分钟
6. 输出结果 JSON

## 状态文件

- `~/.xhs/marketing/daily_state.json` — 每日计数
- `~/.xhs/marketing/circuit_breaker.json` — 熔断器状态

## 定时任务配置

使用 `openclaw cron` 设置定时执行，推荐配置：

- 每天 3-4 次，分布在高峰时段
- 每次处理 3-5 条
- 每日总量不超过 15 条
- **超时设置：`--timeout-seconds 3600`（1小时）**——脚本内置 3-8 分钟随机延迟，5 个关键词预计需要 50+ 分钟，默认超时会导致任务被终止

## 失败处理

- **熔断器触发**：等待自动恢复或手动删除 `circuit_breaker.json`
- **每日上限**：等待次日自动重置
- **非活跃时段**：等待进入时间窗口
- **Chrome 未启动**：先运行 `chrome_launcher.py`
- **未登录**：先执行 xhs-auth 登录
- **IP 被风控**：见下方代理配置

## 代理配置（IP 风控应对）

当 IP 被小红书标记为风险 IP 时（页面显示"安全限制"），有以下选择：

### 方式 1: 等待自动解除
通常 24 小时后风控自动解除，期间暂停所有 cron 任务。

### 方式 2: 更换代理 IP
通过 `--proxy` 参数或 `XHS_PROXY` 环境变量设置代理：

```bash
# SOCKS5 代理
XHS_PROXY=socks5://127.0.0.1:1080

# HTTP 代理（带认证）
XHS_PROXY=http://user:pass@proxy.example.com:8080

# 在 auto_marketing.py 中直接传参
python auto_marketing.py --proxy socks5://127.0.0.1:1080 ...
```

⚠️ 更换代理后需要 **重启 Chrome**（代理是 Chrome 启动参数）：
```bash
python chrome_launcher.py stop
XHS_PROXY=socks5://127.0.0.1:1080 python chrome_launcher.py start --headless
```

### 推荐代理类型
- **住宅代理**（Residential Proxy）：最不容易被检测，推荐 Bright Data、SmartProxy、IPRoyal
- **SOCKS5 代理**：延迟低，适合浏览器自动化
- **避免使用数据中心代理**（DataCenter）：容易被小红书识别和封禁
