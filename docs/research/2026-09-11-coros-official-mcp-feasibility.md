# COROS 官方 MCP 替换社区 stdio 接入的可行性

> 调研日期：2026-09-11。范围仅含 COROS 官方仓库、其公开的 MCP/OAuth 元数据和 LangChain 官方文档；未使用真实 COROS 账号登录，也没有读取任何用户数据。
>
> 实现状态（2026-09-12）：本文保留为改造前的可行性证据，其中“落 MySQL、保留社区组件、双跑”是当时的备选建议，不是当前架构。当前代码改为 OAuth 后按请求读取并在内存中聚合，已删除社区 stdio、缓存与 `fitness_data`；真实 HTTPS callback、tools/list schema、分页和无状态协议仍须通过预发测试账号 PoC 验证。

## 结论

**有条件可行，但不是直接替换。**

COROS 官方远程 MCP 已覆盖 FitAgent 所需的活动、每日健康和睡眠读取能力，并且 HTTP + OAuth 能移除社区 `cygnusb/coros-mcp` 的本地子进程、SQLite 缓存和手写 stdio JSON-RPC 生命周期。它没有公开的「同步本地缓存」接口，因此当前实现保留一层很薄的、非 Agent 的实时读取边界：每位用户 OAuth、只读工具白名单、白名单载荷映射和明确的局部失败契约；不保存运动数据。

在这几个前置条件未实现前，**不能**删除社区组件：

1. 每位 FitAgent 用户能独立完成并撤销 COROS OAuth，令牌按用户加密保存和刷新；不能复用当前进程级单例或官方 Skill 的本机 token 文件。
2. 用测试 COROS 账号执行 `tools/list`、读取三个目标工具的实际 schema 与分页/日期参数，并对照项目既有三类记录做字段映射测试。
3. 只允许调用 `querySportRecords`、`queryDailyHealthData`、`querySleepData`；不把官方动态工具表直接暴露给聊天 Agent。
4. 先双跑并比对数据，再移除社区安装脚本、Runner、stdio 配置和相应依赖。

## 与现有设计的逐项对照

| FitAgent 约束 | 官方证据 | 结论与影响 |
| --- | --- | --- |
| 用户点击 `POST /api/fitness/sync` 才刷新 | 官方 README 只列出远程 MCP 读取工具，未列出缓存刷新、批量同步或计划任务；当前服务的 `sync_cache()` 正是社区私有缓存步骤。[现有 `coros_client.py`](../../app/services/coros_client.py) | **可保留显式触发，但语义改为“当次远程拉取并落 MySQL”。** 删除 `sync_cache`，而不是寻找不存在的官方等价物。 |
| 活动、日指标、睡眠 | 官方工具表含 `querySportRecords`、`queryDailyHealthData` 和 `querySleepData`，分别描述了活动筛选、日健康总览、睡眠评分/阶段等。[COROS 官方 README：Tool List](https://github.com/coroslab/COROS-MCP/blob/main/README.md#tool-list) | **功能表面匹配。** 但公开 README 未给参数和响应 schema；必须在授权后的 PoC 中确认日期格式、分页、字段名和单位后再替换现有 `list_activities/get_daily_metrics/get_sleep_data`。 |
| 用户级授权、多租户 | 官方接入方式是 HTTP OAuth；官方 Skill 使用 `openid offline_access mcp.tools`，授权码 + PKCE，保存 access/refresh token 并在临近过期时刷新。[接入说明](https://github.com/coroslab/COROS-MCP/blob/main/README.md#setup-guide)；[官方 OAuth 实现](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py#L25-L32) [（换码/刷新）](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py#L431-L463) | **有条件匹配。** 官方 Skill 默认把令牌写在单机、按区域划分的 `~/.coros-mcp-skill-gateway/...` 文件中，适合本地客户端而不是多用户后端。[TokenStore](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py#L122-L149) FitAgent 必须以自身已验证的 `user_id` 关联各自的加密 token 和区域；绝不能使用当前 `get_coros()` 的单例来共享某一 COROS 账号。 |
| 后端安全部署 | 官方 gateway 将请求导向 CN/EU/US 区域，并支持直接使用区域 URL；官方实现通过 `Authorization: Bearer ...` 访问 `/mcp`，接受 JSON 或 SSE 响应，采用无状态 MCP 流程。[区域说明](https://github.com/coroslab/COROS-MCP/blob/main/README.md#4-mcp-url-is-invalid)；[HTTP 配置](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py#L465-L481)；[无状态调用](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py#L497-L555) | **传输层适合服务端，但 OAuth 回调与 token 存储须由 FitAgent 实现。** 官方 Skill 固定使用本地回调 `http://127.0.0.1:43123/callback`，不能直接拿来部署 Web 后端。[默认回调](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py#L25-L29) |
| 仅读取，不让聊天 Agent 写训练计划 | 官方 README 当前把 `generateTrainingPlan`、`updateTrainingPlan` 标为 `coming soon`；工具目录会随服务演进变化。[Training Management 工具状态](https://github.com/coroslab/COROS-MCP/blob/main/README.md#tool-list) | **必须在客户端强制 allowlist。** 官方 README 未说明可由客户端请求只读 toolset，也不能假定所有工具都有安全注解。即使 LangChain 可读取 MCP `read_only_hint` / `destructive_hint`，这些字段是服务端可选元数据，不能成为唯一防线。[LangChain 工具元数据](https://docs.langchain.com/oss/python/langchain/mcp/tools#tool-metadata) |

## 官方协议与能力边界

### 传输、认证和会话

- 官方入口为 `https://mcp.coros.com/mcp`，README 称其为 **HTTP OAuth**；重定向不兼容时可使用 CN/EU/US 区域入口。[官方 README](https://github.com/coroslab/COROS-MCP/blob/main/README.md#setup-guide)
- 本次未认证探测得到 `401` 与 bearer 资源元数据；中国区元数据列出 `openid`、`mcp.tools`、`offline_access` scope，bearer header 传递方式、授权码/刷新授权和 PKCE S256 支持。[官方受保护资源元数据](https://mcpcn.coros.com/.well-known/oauth-protected-resource/mcp)；[官方 OpenID 配置](https://mcpcn.coros.com/.well-known/openid-configuration)
- 官方仓库实现使用 JSON-RPC `initialize`、`tools/list` 和 `tools/call`，并明确不发送 `Mcp-Session-Id` 与 `notifications/initialized`；这是 HTTP 无状态流程，和 FitAgent 当前的行分隔 stdio 进程、2024-11-05 初始化通知序列不兼容。[官方实现](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py#L657-L714)
- OIDC 元数据虽列出 `client_credentials`，但官方 README/仓库没有说明 FitAgent 能否获得该类型 client、或它能否访问某个个人 COROS 账户的数据。因此不能把它当成多租户服务账号方案；应以每用户授权码 + PKCE 为默认方案，除非 COROS 另行确认。

### 数据、写入、限额和事件

- 当前 README 已声明活动、日健康、睡眠读取工具；也声明训练计划创建/更新仍是 `coming soon`。除了 README 列出的工具，官方仓库没有给出 Resources、Prompts 或 webhook 的公开契约。因此本迁移只依赖 `tools/list` / `tools/call`，不依赖未文档化的 MCP 能力。
- 已文档化的唯一每日配额是：每个账户每天最多下载 50 个活动 `.fit` 文件；这不是当前三类同步需要调用的接口。健康快测和压力时序工具标注最大查询窗口 7 天。[官方 FAQ](https://github.com/coroslab/COROS-MCP/blob/main/README.md#9-can-coros-mcp-retrievefit-files)；[工具表](https://github.com/coroslab/COROS-MCP/blob/main/README.md#tool-list)
- 官方 README/仓库**没有文档化**活动、日健康、睡眠读取的一般速率限制，也没有数据变更 webhook、增量游标或后台轮询 API。Skill 中的 3 秒轮询是浏览器登录的 claim 流程，不是数据同步机制。[登录轮询实现](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py#L572-L610)

## LangChain 官方组件能简化什么

可以，但要将它用在**远程 MCP 客户端**，而非把 COROS 变成聊天 Agent 的无约束工具包。

LangChain 当前的 `langchain.mcp.MCPAdapter` 可用 HTTP URL 连接 streamable HTTP MCP，负责传输推断、协议协商、连接管理和认证，并将 `list_tools()` 发现的工具适配成 LangChain tools。[LangChain MCP 概览](https://docs.langchain.com/oss/python/langchain/mcp) 它也支持交给 FastMCP 的 OAuth 2.1 与每用户凭据模式。[LangChain OAuth 指南](https://docs.langchain.com/oss/python/langchain/mcp/auth)

这会移除本项目里以下**协议胶水**：`subprocess.Popen`、stdin/stdout 读线程、请求 ID 串行化、进程超时销毁/重建、社区 SQLite cache Runner。它不会移除以下**业务边界**：OAuth connect/callback、token 加密与撤销、区域路由、调用超时/重试、三种官方响应到现有字段的规范化、MySQL 幂等写入、局部失败的 `partial/unavailable_sources` 响应。

另有一个明确的依赖条件：项目目前固定 `langchain==1.3.1`，而当前官方 `langchain.mcp` 文档要求 `langchain[mcp]>=1.4.0` 且标注为 beta。[LangChain 安装说明](https://docs.langchain.com/oss/python/langchain/mcp#install) 因而需要先在单独分支升级并回归测试 LangChain/LangGraph/DeepSeek 调用链；不能仅新增一行 import。旧版 `langchain-mcp-adapters` 的迁移路径也应作为兼容性参考，而不应与新 `langchain.mcp` 混用。

若不升级到 `langchain.mcp`，当前 [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters/blob/52a4535f3eb4b98f386836e4d9b8c4cadf99afca/pyproject.toml#L13-L21) 的 `streamable_http` 连接可省去 HTTP 协议处理，但它公开的连接参数是静态 `headers` 或 `httpx.Auth`，而非“已完成浏览器授权、持久化每用户 refresh token”的高层 OAuth 回调组件。[连接定义](https://github.com/langchain-ai/langchain-mcp-adapters/blob/52a4535f3eb4b98f386836e4d9b8c4cadf99afca/langchain_mcp_adapters/sessions.py#L164-L192) 因此它适合作为**已经取得并刷新 bearer token 之后**的传输客户端，不能替代 FitAgent 的 OAuth 连接闭环。由于这条同步链路不需要把工具交给 Agent，自行封装 MCP Python SDK 的 streamable-HTTP session 也是更窄、更容易做 allowlist 的选择；两种方案都须通过同一组 OAuth、schema 和数据入库契约测试后再选定。

## 推荐的迁移路径

1. **先建端口，不改路由语义。** 把当前 `CorosClient` 抽象成仅含“按日期拉活动、日健康、睡眠”的数据源接口；`POST /api/fitness/sync` 仍由前端显式点击，聊天 Agent 仍没有同步权限。
2. **实现用户连接闭环。** 增加连接、OAuth 回调、断开三个后端端点；callback 的 `state` 必须绑定已登录 FitAgent 用户和 PKCE verifier。按用户加密保存 refresh/access token、到期时间和区域 issuer，刷新失败要求该用户重新连接。token、工具入参、原始健康数据都不写日志。
3. **实现 `OfficialCorosDataSource`。** 使用 `MCPAdapter`/FastMCP HTTP client，但只加载并调用固定 allowlist；每个请求按当前 `user_id` 创建带该用户认证的 client，不做跨用户 adapter、响应或 token 缓存。为 `tools/list` 结果和三个实际 schema 写契约测试，发现工具缺失或 schema 不兼容即拒绝同步。
4. **实现可控的同步事务。** 单次点击直接向远程 COROS 拉取指定日期范围、规范化后按现有 `(user_id, data_type, external_id)` upsert。保留每个源独立失败的 `partial` 契约；对未知的一般 API 限制设置保守的客户端并发上限、超时和退避。不要自行添加后台轮询或 webhook 假设。
5. **影子比对后切换。** 使用同一测试账号、相同 7/28 天范围分别读取两端，对日期、活动 ID、数量、核心指标与睡眠记录比对；连续通过后再以 feature flag 切换生产。保留社区实现作为可回退路径一个发布周期，随后才删除 `.tools` 虚拟环境安装脚本、`coros_mcp_runner.py`、stdio 配置和对应说明。

## PoC 的验收门槛

- 用户 A 与用户 B 分别授权后，A 的 token/client/缓存永不用于 B；断开 A 不影响 B。
- `tools/list` 确认三个白名单工具可用，且实际请求可完成 7 天和 28 天的活动、每日健康、睡眠导入。
- 同日多次活动仍按官方稳定 activity ID（或明确的稳定回退键）并存；重复同步不会重复插入。
- 工具超时、401、token 刷新失败、单一数据源失败均有可测试的 API 契约；原始 COROS 令牌与健康载荷不进入错误日志。
- 训练计划相关或未来新增工具即使出现在官方 catalog，也不会被 importer 或聊天 Agent 调用。

## 读取的主要来源

- [COROS-MCP 官方 README（main）](https://github.com/coroslab/COROS-MCP/blob/main/README.md)，本次核对 commit [`178c5c3`](https://github.com/coroslab/COROS-MCP/tree/178c5c3bbe952543b13f7fbb33297818b76d4804)
- [COROS 官方 Gateway Skill 与登录实现](https://github.com/coroslab/COROS-MCP/tree/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway)
- [COROS CN 区 OpenID 配置](https://mcpcn.coros.com/.well-known/openid-configuration) 与 [MCP 受保护资源元数据](https://mcpcn.coros.com/.well-known/oauth-protected-resource/mcp)
- [LangChain 官方 MCP 文档](https://docs.langchain.com/oss/python/langchain/mcp)、[认证文档](https://docs.langchain.com/oss/python/langchain/mcp/auth)、[工具安全元数据文档](https://docs.langchain.com/oss/python/langchain/mcp/tools)

## 补充 PoC 探测（2026-09-12；未登录、未读取用户数据）

### 已核实的协议契约

- 官方 [CHANGELOG](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/CHANGELOG.md#L5-L27) 明确：2026-06-24 为新的无状态 MCP endpoint 更新了 Skill 调用链，不再依赖 `Mcp-Session-Id`；2026-05-19 将三地区入口收敛为 gateway `https://mcp.coros.com/mcp`。因此现有 stdio 客户端的 `notifications/initialized` 与共享进程设计不能复用到官方 HTTP endpoint。
- 官方 [Skill](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/SKILL.md#L31-L35) 规定：先从 gateway 的 OpenID discovery 取 issuer，随后将**登录、刷新 token、MCP 调用和本地状态**固定到得到的 CN/EU/US issuer；其说明还明确无状态运行时不传 session header、不发初始化通知。[Skill Notes](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/SKILL.md#L76-L89) 对后端而言，这意味着用户连接记录至少要持久化 `issuer`，不能只保留 gateway URL。
- 本次对 gateway、CN、EU、US 四个公开 `.well-known/openid-configuration` 与 `.well-known/oauth-protected-resource/mcp` 端点的只读 GET 均发现：每个区域声明相应的 `issuer`、`authorization_code` / `refresh_token` grant、PKCE `S256`、`openid mcp.tools offline_access` scope、`registration_endpoint`，以及 bearer header 访问 MCP 资源。以 [CN OIDC 元数据](https://mcpcn.coros.com/.well-known/openid-configuration) 和 [CN protected-resource 元数据](https://mcpcn.coros.com/.well-known/oauth-protected-resource/mcp) 为例；EU/US endpoint 返回同结构、区域化 issuer。
- 官方 helper 的 `register_client()` 确实向 `/{issuer}/connect/register` 发送动态注册请求，声明 authorization-code、refresh-token、PKCE 和 `token_endpoint_auth_method: none`；`--redirect-uri` 是可配置参数。[动态注册实现](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py#L285-L319) [（参数）](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py#L717-L727)；授权码换 token 时附 `code_verifier`，刷新时带 refresh token。[官方实现](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py#L431-L463)

### 未能从公开证据确认的事项（迁移风险）

1. **服务端 callback URI 是否可用：未确认。** 仓库只证明 helper 会把其可配置的 URI 送入 DCR，默认却是 loopback `http://127.0.0.1:43123/callback`；没有公开注册策略证明生产 HTTPS callback 会被接受。PoC 应先用测试 COROS 账号做一次 DCR + FitAgent HTTPS callback 验证，再设计用户连接表或删除社区组件。
2. **DCR 的适用条件：未确认。** OIDC 元数据公布 `registration_endpoint`，源码期望 HTTP 200/201，但没有公开文档说明注册配额、允许的域名、client 生命周期或生产服务注册政策。本调研没有 POST 注册，避免创建外部 client。
3. **工具 schema 需授权才能获得：已验证。** 未带 bearer token 向 `https://mcpcn.coros.com/mcp` 发出只读 JSON-RPC `tools/list`，服务返回 `401`，`WWW-Authenticate` 指向官方 protected-resource metadata，响应体为空。官方 helper 也是先 `ensure_token()` / `initialize`，再分页 `tools/list`，并建议在调用陌生工具前读取 schema。[实现](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py#L497-L555) [（Skill 指引）](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/SKILL.md#L37-L59) 所以 README 的工具名不足以完成字段映射，必须在授权 PoC 中保存一份脱敏 schema 快照和响应契约。
4. **工具目录会变动。** 官方 README 要求 MCP 更新后手工刷新工具；Skill 的工具目录缓存 TTL 为 300 秒并支持 `--refresh`。[README FAQ](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/README.md#L307-L322) [（缓存实现）](https://github.com/coroslab/COROS-MCP/blob/178c5c3bbe952543b13f7fbb33297818b76d4804/skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py#L113-L119) 生产 importer 应把 catalog 变化视为需要兼容性检查的事件，不能因新增工具自动扩权。

### 更新后的 PoC 结论

官方证据足以开始 **OAuth + streamable-HTTP + 固定只读工具白名单** 的隔离 PoC；不足以批准直接删除社区实现。Go/no-go 的第一道门槛是：在不共享 token 的前提下，验证生产 HTTPS redirect URI 的 DCR、每用户 refresh、以及三个读取工具的授权后 schema。任何一项失败，都应继续保留现有社区实现作为回退。
