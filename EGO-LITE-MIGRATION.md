# 运行方式迁移与旅行 MCP 状态

## 小红书：ego-lite

- 所有 `skills/*/SKILL.md` 以 ego-lite 执行规则为准；旧 CLI/扩展文档只作历史资料。
- 使用 `ego-browser` 技能，通过用户当前登录页面的可见 UI 操作。
- 不读取 Cookie/令牌，不绕验证码或风控。需用户登录时交还页面。
- 搜索/阅读可直接执行；发布、评论、点赞、收藏等账号写入动作逐项确认后再提交，并核对页面结果。

## 旅行 MCP

| 服务 | 状态 | 备注 |
|---|---|---|
| 12306 | 已写入 Hermes MCP 配置 | 本地 stdio，`npx -y 12306-mcp`；已完成 initialize 和 tools/list 握手，服务提供余票、车站、经停及中转查询。 |
| 高德地图 | 待凭据 | 官方 MCP：`https://mcp.amap.com/mcp?key=...`。需用户在高德开放平台创建 Web 服务 Key。 |
| 飞猪 | 待连接凭据 | ModelScope 的 FliggyTravel 托管 MCP 覆盖机票、酒店、火车、景点和交通；连接需部署实例的专属 URL 与 `FLYAI_API_KEY`，文档标示 24 小时有效。 |
| 携程 | 未单独接入 | 目前发现的公开实现是低采用度的 Playwright 抓取服务，带反爬/验证风险；不作为可靠 MCP 上线。可先用飞猪聚合服务覆盖旅行查询，若必须用携程直连，再评估用户认可的数据源。 |

### 待用户提供

1. 高德 Web 服务 Key（建议在本机终端通过安全方式配置，不要贴在聊天里）。
2. 飞猪托管 MCP 的专属 Streamable HTTP URL 和 `FLYAI_API_KEY`（通过 ModelScope 部署后取得；密钥不要贴在聊天里）。
3. 是否接受飞猪 MCP 作为默认航班/酒店来源，并把携程作为未接入的备选来源。

凭据提交前，不会在仓库、提交记录或对话中存储密钥。配置实际凭据后需重启 Hermes，再独立验证 MCP initialize 与 tools/list。
