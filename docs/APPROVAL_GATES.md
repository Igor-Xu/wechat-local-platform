# Approval gates

Normal bounded reads are the only operations in the production MCP surface.
Each operation below remains outside the normal server until its own scope,
destination and rollback are approved.

1. **Key acquisition** — process-module discovery, memory reads, Hook or
   restart. The key-agent is a separate future milestone and never runs from
   MCP.
2. **Persistent media work** — `.dat` decoding cache or image-key refresh.
3. **Chat export** — a user-selected chat, exact format and destination.
4. **Codex configuration** — backup, exact entry, permissions, impact and
   restore command shown before writing.
5. **New-host or changed-runtime rollout** — live end-to-end acceptance must
   pass before an explicit Codex configuration change; a restarted, genuinely
   new Codex task must then verify the exact 18-tool surface and safety status.

No approval can enable snapshot fallback or arbitrary SQL. Those behaviors are
intentionally absent from this product boundary.
