# Changelog

## Unreleased

- Reworked the public README around the accepted direct-only 18-tool Beta,
  including architecture, safety boundaries, installation and Codex setup.
- Added the MIT license and a Chinese Windows amd64 Beta usage guide.
- Replaced the machine-private example's workstation-specific path with public
  placeholders and documented the locked Python identity fields.
- Updated architecture, capability, support and approval documents to reflect
  the completed production switch while keeping new-host rollout gates.
- Clarified that the adapter does not persist successful payloads, while
  requested content can still enter the Codex task and be subject to the
  applicable Codex product and organization data controls.

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
