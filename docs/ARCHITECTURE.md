# Architecture

```text
Weixin encrypted DB/WAL
        │
        ├── bootstrap/key-agent (future, occasional, separately approved)
        │       └── candidate key → per-DB Page-1 HMAC → managed key map
        │
        └── direct reader (daily, strict read-only)
                └── pinned wechat-cli v1.6.20 + libWCDB
                        └── stable adapter contract
                                └── stdio MCP → Codex
```

## Responsibilities

The key-agent may eventually handle version-specific discovery, process-module
compatibility and HMAC verification. It is never started by the MCP server and
does not share a resident process with the daily reader. The daily reader only
uses an already verified key map.

The adapter owns policy, not database decryption: fixed executable identity,
fixed argv construction, strict environment variables, schema bounds, in-memory
chat resolution, JSON-RPC framing, status redaction and error redaction.

The upstream engine owns WCDB page reads, message parsing, live FTS, media
resource association, Moments parsing and the structured result shape.

## Data flow

1. Codex calls an allowlisted read tool.
2. The adapter validates only the public schema.
3. A human chat label, if present, is resolved from direct sessions/contacts in
   memory. An ambiguous label stops the call.
4. The adapter sends one fixed `call-json` argv to the pinned executable.
5. The child receives the two forced strict-read-only environment variables.
6. The successful JSON envelope is returned without a plaintext staging file.

The only intentional transformations are the local resolver result, metadata-
only status, and redacted errors. No fallback branch exists.

## Pagination and context

Offset is retained for compatibility, while timeline callers can use time
filters and before/after message anchors. This avoids offset drift when new
messages arrive. Context is an exact anchor operation: local/server message
identifiers must come from a prior requested result or be explicitly supplied.

## Production state

The accepted deployment uses this repository's adapter as the sole production
source for `wechat_local_access_windows`. Its normal path is direct-only and
contains no snapshot or fallback branch. The original rollout passed live
DB/WAL immutability checks, all 18 structured tool scenarios and a genuinely
new Codex-task inventory/status check.

That evidence is machine-specific. A new host, account, Weixin version,
runtime, key map or public schema requires a new rollout: first offline tests,
then separately approved live acceptance, then an explicit Codex configuration
change and new-task check. A failed rollout must preserve the previous working
configuration; it must not introduce snapshot fallback.
