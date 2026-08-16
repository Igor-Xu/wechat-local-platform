# Local WeChat platform development boundary

This repository contains the production source for the project-configured
`wechat_local_access_windows` server. The accepted deployment exposes exactly
the eighteen tools in `adapter/server.py`; historical nine-tool adapters and
rollout backups are audit material, not production fallbacks.

The production server must always:

- use the locked Windows amd64 `wechat-cli.exe` and `libWCDB.dll`;
- launch an absolute executable with an argv array and `shell=false`;
- force `WECHAT_CLI_STRICT_READ_ONLY=1` and
  `WECHAT_CLI_DISABLE_AUTO_REFRESH=1`;
- use only the static read-only tool allowlist in `adapter/server.py`;
- resolve human chat labels from direct `sessions` and `contacts` rows in
  adapter memory, without a resolver subprocess or persistence;
- preserve successful upstream read payloads, including bounded content,
  ordinary identifiers and cursors, when the user asks to inspect them;
- redact status and upstream errors;
- never add SQL, send, update, export, cache refresh/rebuild, key extraction,
  companion, elevation, or fallback behavior to the normal MCP surface.

Offline tests may use synthetic payloads and the locked binary's static
catalog/schema commands. A live acceptance must be a separate, evidence-based
run with the user-selected account and immutable DB/WAL baselines. No private
configuration, key map, account directory, database, snapshot, cache, export,
or message body may be committed.
