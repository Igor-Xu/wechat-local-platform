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

## Production migration

The candidate project remains beside the current production adapter until:

- the live DB/WAL hash baseline and post-run comparison pass;
- all 18 tools that are present on this machine complete structured calls;
- a new Codex task validates the intended MCP surface;
- snapshot-to-direct rollback is rehearsed;
- the configuration switch is separately approved.
