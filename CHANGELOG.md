# Changelog

## 0.1.1 - 2026-08-16

- Enabled the complete eighteen-tool MCP surface in the machine-global Codex
  configuration and verified it in a restarted, genuinely new task.
- Restored the ignored machine-private launcher configuration without adding
  any database, snapshot, cache, index, export or key material to Git.
- Split source inventory diagnostics into all configured databases and the
  database families actually addressed by the public eighteen-tool surface.
- Added a regression test proving that a new unaddressed database family is
  disclosed in aggregate without leaking filenames, paths, salts or keys.
- Added a tracked-files-only local release builder with an external SHA-256
  file manifest.

## 0.1.0 - 2026-08-14

- Established the locked `wechat-cli v1.6.20 + libWCDB` direct reader.
- Added the fail-closed eighteen-tool stdio MCP adapter, strict read-only
  environment enforcement, in-memory chat resolver and metadata-only status.
- Completed the initial live acceptance and project-level Codex rollout.
