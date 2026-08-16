# wechat-local-platform

> **Beta / Windows amd64 only**

`wechat-local-platform` 是一个面向 Codex 的本地微信只读访问平台：它把锁定的
`wechat-cli v1.6.20 + libWCDB` 作为 direct 读取引擎，再通过一个薄的 stdio MCP
适配器提供 18 个结构化工具。

数据库解密、读取和 MCP 适配在本机完成：

```text
Weixin 加密 DB/WAL
        ↓
已验证的 per-DB key map（机器私有，不进入 Git）
        ↓
wechat-cli v1.6.20 + libWCDB（direct / strict read-only）
        ↓
wechat-local-platform adapter（固定工具、参数与错误边界）
        ↓
stdio MCP
        ↓
Codex
```

用户发起内容查询时，有限正文和结构化结果会进入 Codex 当前任务上下文，并可能按用户所用
Codex 产品、账号和组织策略由模型服务处理或保留。项目本身不上传数据库、key 或原始文件，
也不会把查询结果另写到本地明文存储；它不能替代你对 Codex 数据控制设置的判断。

当前测试版：[v0.1.1 Beta](https://github.com/Igor-Xu/wechat-local-platform/releases/tag/v0.1.1)

## 当前验证状态

| 项目 | 状态 |
|---|---|
| 平台 | Windows amd64 |
| 本机验证微信 | Weixin 4.1.12.26 |
| 读取引擎 | 固定 `wechat-cli v1.6.20 + libWCDB` |
| 后端 | `direct` |
| 原始 DB/WAL | 严格只读，验收前后内容与 mtime 差异均为 0 |
| MCP 工具 | 18 个 |
| 自动刷新 | 禁用 |
| snapshot / plaintext index | 不创建、不读取 |
| 自动 fallback | 不存在 |
| 新 Codex 任务 | 已完成真实枚举与 metadata-only `status` 验收 |

这是一台真实机器上的验证结果，不代表所有 Windows 或 Weixin 版本都已兼容。

## MCP 工具

| 类别 | 工具 |
|---|---|
| 状态与会话 | `status`、`sessions`、`unread` |
| 会话解析与消息 | `resolve_chat`、`chat_timeline`、`message_context`、`read_events` |
| 搜索 | `search`、`search_with_context` |
| 联系人和群 | `contacts`、`group_members` |
| 媒体和收藏 | `media_resources`、`favorites` |
| 朋友圈 | `moments_feed`、`moments_search`、`moments_notifications` |
| 支付记录 | `transfers`、`red_packets` |

正常内容查询会保留上游结构化结果，包括用户明确请求的有限正文、正常消息标识、
分页游标和已经存在的可读媒体路径。`status` 始终只返回 metadata；上游错误会脱敏。
适配器不会把成功响应写入快照、正文索引、缓存、日志、报告或临时文件。

## 安全边界

普通 MCP 永远不暴露：

- 任意 SQL、数据库 schema 直查、原始页面或密钥接口；
- 发送消息、自动回复、控制微信 UI、协议登录；
- 自动取钥、进程内存读取、Hook、注入或提权；
- cache refresh/rebuild、snapshot、plaintext body index；
- 自动或静默 fallback；
- 本地聊天导出和持久化媒体解码。

取钥、导出、持久媒体处理和新机器接入都属于独立维护动作，必须单独授权；它们不在
18 工具 MCP 中。详见 [安全批准门](docs/APPROVAL_GATES.md) 和
[威胁模型](docs/THREAT_MODEL.md)。

## Beta 快速开始

完整步骤和停止条件见 [Beta 使用指南](docs/BETA_USAGE.zh-CN.md)。以下只给出最短路径。

### 1. 前置条件

- Windows amd64；
- Python 3.11 或更高版本；
- 本人所有或已明确授权的本地微信数据；
- 已验证的 schema-2 per-DB key map；
- 下载并核验本项目固定 Release，禁止用不明二进制替换 runtime。

本项目不会从 GitHub Release 中提供任何账号 key、微信数据库或机器私有配置。
如果尚无逐库 HMAC 验真的 schema-2 key map，当前 Beta 不能直接完成首次接入；自动 key-agent
仍是后续里程碑，不能用来源不明的 key 工具、关闭系统安全策略或生成明文 snapshot 绕过。

### 2. 核验 Release

下载 ZIP、`.zip.sha256` 和 `FILE_MANIFEST.json` 后，在 PowerShell 中执行：

```powershell
$zip = ".\wechat-local-platform-0.1.1.zip"
$sidecar = ".\wechat-local-platform-0.1.1.zip.sha256"
$expected = ((Get-Content -LiteralPath $sidecar -Raw).Trim() -split "\s+")[0].ToLowerInvariant()
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "release SHA-256 mismatch" }
```

### 3. 创建机器私有配置

在解压后的项目目录中：

```powershell
New-Item -ItemType Directory -Force .\.local | Out-Null
Copy-Item .\config\machine-private.example.json .\.local\machine.json
```

> `v0.1.1` Release 内的示例早于 Python 运行时锁定字段，不能原样使用。请对照当前
> [`machine-private.example.json`](config/machine-private.example.json) 补齐
> `python_exe` 和 `python_sha256`；下一版 Beta 会把修正后的示例收入发行包。

编辑 `.local\machine.json`，填写本机绝对路径，并替换 Python SHA-256：

```powershell
(Get-FileHash -Algorithm SHA256 -LiteralPath "C:\Path\To\python.exe").Hash.ToLowerInvariant()
```

`.local/`、key map、微信数据库和验收产物都在 Git 忽略范围内，不得使用 `git add -f`
强制提交。

### 4. 运行离线验收

```powershell
.\scripts\acceptance_check.ps1 -Python "C:\Path\To\python.exe"
```

该命令不读取真实微信正文，只验证策略、工具白名单、固定哈希和 schema。

### 5. 配置 Codex MCP

根据 [OpenAI 官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)，
用户级配置位于 `~/.codex/config.toml`；可信项目也可使用 `.codex/config.toml`。

以下示例中的路径必须替换为本机绝对路径：

```toml
[mcp_servers.wechat_local_access_windows]
command = "C:\\Path\\To\\python.exe"
args = ["-u", "-m", "adapter", "--config", "C:\\Tools\\wechat-local-platform\\.local\\machine.json"]
cwd = "C:\\Tools\\wechat-local-platform"
enabled = true
required = true
startup_timeout_sec = 30
tool_timeout_sec = 180
enabled_tools = ["status", "sessions", "unread", "resolve_chat", "chat_timeline", "message_context", "search", "search_with_context", "read_events", "contacts", "group_members", "media_resources", "favorites", "moments_feed", "moments_search", "moments_notifications", "transfers", "red_packets"]
disabled_tools = []
env = { WECHAT_CLI_STRICT_READ_ONLY = "1", WECHAT_CLI_DISABLE_AUTO_REFRESH = "1" }
```

修改后彻底重启 Codex，并在真正新建的任务中确认：

- 工具恰好为 18 个；
- `backend_used=direct`；
- `live_read_ok=true`；
- `metadata_only=true`；
- `strict_read_only=true`；
- auto refresh 已禁用；
- fallback 已禁用。

## 已知限制

- Beta 不包含自动 key-agent；缺 key 时会失败关闭，不会自动扫描进程或降级到 snapshot。
- 锁定的 v1.6.20 工具面尚未读取新出现的 chatbot 数据库族。
- 微信升级可能新增 schema 或 DB salt，需要重新审计和单独批准的 key 维护。
- 媒体工具只返回已经存在且可读的本地资源；不会自动生成解码缓存。
- `readiness=degraded` 本身不是失败结论，应同时检查 `live_read_ok` 和真实查询。
- 本项目不是腾讯或微信官方产品。

## 开发与验收

```powershell
# 离线测试与策略验收
.\scripts\acceptance_check.ps1 -Python "C:\Path\To\python.exe"

# 真实数据验收：仅在微信完全退出后执行
"C:\Path\To\python.exe" .\scripts\live_acceptance.py --config "C:\Absolute\Path\machine.json"
```

真实验收会比较原始 DB/WAL 前后 SHA-256 和 mtime，并生成 metadata-only 报告。它不会把
消息正文写入报告。详细流程见 [Live acceptance](docs/LIVE_ACCEPTANCE.md)。

## 文档

- [Beta 使用指南](docs/BETA_USAGE.zh-CN.md)
- [架构](docs/ARCHITECTURE.md)
- [能力矩阵](docs/CAPABILITY_MATRIX.md)
- [支持矩阵](docs/support-matrix.md)
- [威胁模型](docs/THREAT_MODEL.md)
- [批准门](docs/APPROVAL_GATES.md)
- [上游审计](provenance/UPSTREAM_AUDIT.md)
- [版本记录](CHANGELOG.md)

## License

本项目原创部分采用 [MIT License](LICENSE)。vendored `wechat-cli`、`libWCDB` 和其他
第三方组件仍分别受其原许可证与 [THIRD_PARTY_NOTICES](runtime/windows-amd64/THIRD_PARTY_NOTICES.md)
约束。

请只处理本人所有或已明确授权的数据，并遵守当地法律、组织政策及软件许可。
