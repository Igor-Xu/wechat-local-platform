# Beta 使用指南（Windows amd64）

本文面向首次在 Windows 上接入 `wechat-local-platform` 的用户。Beta 的目标是验证
“本地加密 DB/WAL → strict direct reader → 18 工具 MCP → Codex”闭环，不是无人值守的
一键解密器。

## 1. 使用前先确认

只有同时满足以下条件才继续：

- 电脑是 Windows amd64；
- 微信账号和本地数据属于你本人，或你有明确授权；
- 你接受消息正文在用户发起查询时进入 Codex 当前任务上下文，并按你所用 Codex 产品、
  账号和组织的数据策略由模型服务处理或保留；
- 你不会把 key、原始数据库、账号标识或私人路径贴到公开 Issue；
- 你理解本项目不是腾讯或微信官方产品。

不满足授权条件时，请停止。

## 2. Beta 的安全模型

日常 MCP 只运行读取链路：

```text
固定 Python → adapter → 固定 wechat-cli.exe argv → libWCDB → 原始加密 DB/WAL
```

适配器强制：

```text
WECHAT_CLI_STRICT_READ_ONLY=1
WECHAT_CLI_DISABLE_AUTO_REFRESH=1
```

并且没有 snapshot、SQLite 明文副本、正文索引、自动 fallback、任意 SQL、发送、导出、
更新、refresh/rebuild 或取钥工具。

成功内容调用可能返回有限正文、消息标识、游标和已存在的媒体路径。适配器不把这些值
另写入本地快照、索引、缓存、日志、报告或临时文件；这不代表 Codex 当前任务或模型服务
不会按其数据策略处理或保留内容。`status` 始终 metadata-only。

## 3. 准备发行包

从 GitHub Release 下载同一版本的三个文件：

- `wechat-local-platform-<version>.zip`
- `wechat-local-platform-<version>.zip.sha256`
- `wechat-local-platform-<version>.FILE_MANIFEST.json`

先核验 ZIP：

```powershell
$version = "0.1.2"
$zip = ".\wechat-local-platform-$version.zip"
$sidecar = ".\wechat-local-platform-$version.zip.sha256"
$expected = ((Get-Content -LiteralPath $sidecar -Raw).Trim() -split "\s+")[0].ToLowerInvariant()
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "release SHA-256 mismatch" }
```

哈希不一致时不要解压或运行。

## 4. 准备机器私有配置

解压到固定目录，例如 `C:\Tools\wechat-local-platform`，然后：

```powershell
Set-Location C:\Tools\wechat-local-platform
New-Item -ItemType Directory -Force .\.local | Out-Null
Copy-Item .\config\machine-private.example.json .\.local\machine.json
```

编辑 `.local\machine.json`：

| 字段 | 含义 |
|---|---|
| `python_exe` | 实际运行 adapter 的 Python 绝对路径 |
| `python_sha256` | 上述 Python 文件的 SHA-256 |
| `wechat_cli_exe` | Release 内固定 `wechat-cli.exe` |
| `wcdb_dll` | Release 内固定 `libWCDB.dll` |
| `db_account_root` | 当前账号的微信数据根目录 |
| `managed_key_config` | 已验证 schema-2 per-DB key map |
| `managed_state_dir` | 独立、已存在的本机状态目录 |
| `cli_timeout_seconds` | 单次上游调用超时，范围 1–120 秒 |

计算 Python 哈希：

```powershell
(Get-FileHash -Algorithm SHA256 -LiteralPath "C:\Path\To\python.exe").Hash.ToLowerInvariant()
```

机器配置和 key map 必须留在 Git 忽略范围内。不要截图、粘贴或提交它们。

如果没有已经逐库 HMAC 验真的 schema-2 key map，请在此停止。当前 Beta 尚不提供自动
key-agent，也不授权运行来源不明的取钥程序、关闭 Defender/UAC 或创建明文 snapshot。

## 5. 离线验收

```powershell
.\scripts\acceptance_check.ps1 -Python "C:\Path\To\python.exe"
```

要求：

- 退出码为 0；
- 20 个测试全部通过；
- runtime 哈希通过；
- `snapshot_created=false`；
- `fallback_enabled=false`。

离线验收不会读取真实聊天正文。

## 6. Codex 配置

OpenAI 官方文档说明，用户级 MCP 配置默认位于 `~/.codex/config.toml`；可信项目也可
使用项目内 `.codex/config.toml`。建议先备份现有配置，再增加 README 中的
`[mcp_servers.wechat_local_access_windows]` 表。

必须满足：

- `command`、`cwd` 和 `--config` 都是绝对路径；
- 参数使用 TOML 数组，不经过 `cmd.exe` 或 PowerShell 字符串拼接；
- `enabled_tools` 恰好是项目公布的 18 个工具；
- strict read-only 和 disable auto refresh 两个环境变量均为 `1`；
- 不配置 snapshot 或 fallback 服务器。

修改后彻底退出并重启 Codex。旧任务不会自动获得新工具。

## 7. 新任务安全验收

在真正新建的 Codex 任务中，先做以下最小检查：

1. 枚举工具，必须恰好 18 个；
2. 只调用一次 `status`；
3. 确认：
   - `backend_used=direct`
   - `live_read_ok=true`
   - `metadata_only=true`
   - `strict_read_only=true`
   - `auto_refresh_disabled=true`
   - `fallback_enabled=false`

任一条件不满足，立即停止；不要改用 shell、SQLite、旧 adapter、快照或缓存。

## 8. 真实读取验收

建议让微信完全退出后进行。至少验证：

- `sessions`；
- exact / unique partial / ambiguous 三种 `resolve_chat`；
- `resolve_chat → chat_timeline`；
- 精确 `message_context`；
- 已知命中的 `search` 和 `search_with_context`；
- `contacts`、`group_members`；
- 朋友圈、收藏、媒体和支付类工具的有界调用。

正式验收脚本：

```powershell
"C:\Path\To\python.exe" .\scripts\live_acceptance.py --config "C:\Absolute\Path\machine.json"
```

报告必须 metadata-only；原始 DB/WAL 前后内容和 mtime 差异必须为 0。SHM 变化单独披露，
不能冒充 DB/WAL 写入。

## 9. 日常使用

- 每个新任务先通过 `status` 安全门；
- 查询时使用联系人昵称、备注或群名，不手工猜内部标识；
- 多结果解析必须返回 ambiguous，不擅自选择；
- 正文只在用户明确发起内容查询时返回；
- 媒体路径只用于用户明确要求查看的本地资源；
- 不把普通删除描述为 SSD 法证级安全擦除。

## 10. 常见问题

### Codex 看不到工具

检查：

- 是否修改了正确的 `~/.codex/config.toml`；
- `command`、`cwd` 和机器配置是否存在；
- `enabled_tools` 是否为 18 个；
- 是否彻底重启 Codex 并新建任务。

### `status` 不可用

停止内容查询。先运行离线验收并检查机器配置、Python/runtime 哈希和绝对路径；不要切换
到 snapshot。

### `readiness=degraded`

不能只凭这一项判失败。继续检查 `live_read_ok`、能力布尔值以及一项真实结构化查询。

### 新数据库缺 key

如果 `public_tool_missing_key_db_count > 0`，停止相关读取。当前 Beta 不提供自动 key-agent；
需要在单独批准的维护流程中重新验证 key，不能通过关闭安全模式或创建快照绕过。

`public_tool_unaddressed_db_count > 0` 表示磁盘上存在当前 18 工具不访问的数据库族；它会
被披露，但不能被误称为已支持。

### 搜索不到最新消息

先比较 `latest_message_db_mtime`、公共工具所需数据库 key 覆盖和 timeline 最新时间。不要
因为 CLI 能启动就断言数据完整。

## 11. 回滚

1. 从 Codex 配置中移除或禁用 `wechat_local_access_windows`；
2. 重启 Codex，确认工具不再出现；
3. 按需删除本项目 runtime 和 `.local` 配置；
4. 不触碰原始微信 DB/WAL/SHM；
5. 单独处理 Windows 凭据或 GitHub CLI 登录，不把它们混入微信数据清理。

## 12. 公开反馈

公开 Issue 中只提供：

- Windows / Weixin / 项目版本；
- 工具名、布尔状态、计数和脱敏日期；
- 不含路径、标识或正文的错误分类。

不要提交 key、token、账号 ID、联系人/群标识、聊天正文、原始数据库、私人路径或完整日志。
涉及安全问题时优先使用 GitHub Security 的私密报告渠道。
