# wechat-local-platform

一个 Windows amd64 本地微信读取平台候选实现。

项目把已经本机验证的 `wechat-cli v1.6.20 + libWCDB` 固定为第一阶段读取引擎，再用一个薄的 stdio MCP 适配器给 Codex 提供结构化工具。取钥、读取、MCP 是三个安全职责：它们可以放在一个仓库和发行包中，但不会变成同一个常驻进程，也不会由 MCP 自动取钥。

## 当前状态

- M1 上游基线已锁定并提交：`a4a73d2`。
- M2 候选 MCP 已实现并通过离线测试。
- 锁定 runtime 的 18 个静态工具/schema 已验证。
- 当前工作区生产 MCP 未切换；没有修改 Codex 配置。
- 没有复制 key map、微信数据库、WAL、快照、缓存或机器私有路径。

## 工具面

候选 MCP 暴露：

`status`、`sessions`、`unread`、`resolve_chat`、`chat_timeline`、`message_context`、`search`、`search_with_context`、`read_events`、`contacts`、`group_members`、`media_resources`、`favorites`、`moments_feed`、`moments_search`、`moments_notifications`、`transfers`、`red_packets`。

成功读取基本保留上游 JSON，包括用户请求的正文、正常消息标识、分页游标和已有可读媒体路径；`status` 和错误响应单独脱敏。适配器不会把正文写入快照、搜索索引、缓存、日志、报告或临时文件。

`resolve_chat` 只从 direct `sessions`/`contacts` 取得昵称、备注和 alias，在 adapter 进程内存中完成精确匹配、唯一部分匹配和歧义拒绝。内部值只用于同一进程随后的一次查询转发，不进入 resolver 的公开结果。

`chat_timeline` 支持时间过滤、offset、前后消息锚点和 server id 字符串；`message_context` 要求调用者明确提供前一次结果中的 local/server message anchor，不会擅自选最新消息。

## 明确不在普通 MCP 中的能力

- 任意 SQL、数据库 schema 直查和原始 XML/密钥接口；
- 发送、控制微信 UI、更新、自动取钥、Hook/注入、提权；
- snapshot、plaintext body index、自动 refresh/rebuild、静默 fallback；
- 本地导出文件；
- 媒体 `.dat` 解码缓存或 image-key 刷新。

`media_resources` 可以返回上游已经解析出的、确实存在且可读的本机媒体路径，但它不创建缓存；需要持久化解码、导出或取钥时，必须走独立批准门。

## 运行方式

先把 `config/machine-private.example.json` 复制为机器私有 JSON，并填入绝对路径。该文件必须留在 `.gitignore` 覆盖范围内：

```powershell
python -m adapter --config C:\private\wechat-local-platform.machine-private.json
```

运行时固定要求：

```text
WECHAT_CLI_STRICT_READ_ONLY=1
WECHAT_CLI_DISABLE_AUTO_REFRESH=1
```

适配器只允许固定的四个私有路径环境变量；它会清理继承来的其他 `WECHAT_CLI_*` 和 `WX_MCP_*` 变量。

## 验收

离线入口：

```powershell
.\scripts\acceptance_check.ps1
```

它只运行合成 payload、策略测试、锁定哈希和 runtime 静态 catalog/schema 检查，并生成 metadata-only 报告。真实微信验收、Codex 新任务、生产切换和删除/迁移操作必须另行展示批准门。

架构与威胁模型见 `docs/ARCHITECTURE.md`、`docs/THREAT_MODEL.md` 和 `docs/APPROVAL_GATES.md`。
