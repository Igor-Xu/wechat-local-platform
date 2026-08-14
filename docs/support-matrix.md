# Support matrix

| Area | v1.6.20 baseline | Candidate project status | Evidence |
|---|---|---|---|
| Windows amd64 runtime | locked release asset | verified locally | `sources.lock.json`, hash test |
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
| fallback | not part of candidate | absent and fail-closed | status contract and code review |
| key acquisition | upstream capability, separate risk | not started by MCP | approval-gate document |

“Catalog/schema attestation” is not a live database acceptance. The complete
matrix becomes production evidence only after the separately planned live
DB/WAL immutability and new-Codex-task checks pass.
