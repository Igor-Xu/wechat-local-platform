# Support matrix

| Area | v1.6.20 baseline | Project status | Evidence |
|---|---|---|---|
| Windows amd64 runtime | locked release asset | verified locally | `provenance/sources.lock.json`, hash test |
| WCDB direct read | bundled libWCDB | delegated to pinned engine | runtime asset and upstream source |
| strict read-only | supported by upstream mode | forced by adapter | argv/environment tests |
| auto refresh disabled | environment switch | always forced to `1` | environment test |
| sessions/contacts resolution | direct upstream rows | in-process exact/partial/ambiguous resolver | resolver tests |
| timeline paging | offset and message/time filters | public bounded schema | schema test |
| exact message context | local/server anchor | anchor is required | context test |
| live FTS | search/search-context | public bounded schema | catalog/schema attestation |
| media resources | upstream agent-ready paths | existing readable paths only | public schema excludes cache/debug switches |
| Moments | sns feed/search/notifications | mapped read-only tools | catalog/schema attestation |
| local plaintext snapshot/index | upstream maintenance feature | absent from normal MCP | allowlist and source review |
| fallback | not required by direct reader | absent and fail-closed | status contract and code review |
| key acquisition | upstream capability, separate risk | not started by MCP | approval-gate document |

“Catalog/schema attestation” alone is not a live database acceptance. On the
accepted machine, the separately recorded DB/WAL immutability run and the new
Codex-task checks passed. Those machine-specific results do not automatically
cover a different host, account, Weixin version, runtime or key map.
