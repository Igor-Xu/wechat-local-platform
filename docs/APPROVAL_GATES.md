# Approval gates

Normal bounded reads are the only operations in the candidate MCP surface.
Each operation below remains outside the normal server until its own scope,
destination and rollback are approved.

1. **Key acquisition** — process-module discovery, memory reads, Hook or
   restart. The key-agent is a separate future milestone and never runs from
   MCP.
2. **Persistent media work** — `.dat` decoding cache or image-key refresh.
3. **Chat export** — a user-selected chat, exact format and destination.
4. **Codex configuration** — backup, exact entry, permissions, impact and
   restore command shown before writing.
5. **Production switch** — live end-to-end acceptance and a new Codex task
   must pass before replacing the existing nine-tool entry.

No approval can enable snapshot fallback or arbitrary SQL. Those behaviors are
intentionally absent from this product boundary.
