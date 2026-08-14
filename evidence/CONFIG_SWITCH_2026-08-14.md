# Project Codex MCP configuration switch — 2026-08-14

The user explicitly approved switching the project-scoped
`wechat_local_access_windows` entry to `wechat-local-platform`.

## Applied change

- Backed up the previous project `.codex/config.toml` and root `AGENTS.md` to
  the ignored local rollout-backup directory.
- Kept the existing MCP server name.
- Kept the existing locked absolute Python command and machine-private config.
- Changed only the MCP `cwd` line to the candidate repository.
- Preserved strict-read-only and disable-auto-refresh environment values.
- Updated the project access policy from the former nine-tool surface to the
  verified eighteen-tool surface.
- Did not modify the user-global Codex config.

## Verification

- Project TOML parse: passed.
- Candidate launcher exit: zero.
- Static tool inventory: exactly 18 and exact-name match.
- Launcher stderr: empty.
- Weixin/WeChat process count after verification: zero.
- No status/content query was used for this post-write smoke test.

The switch is not a complete Codex acceptance until Codex is restarted and a
genuinely new task performs status and real read calls.

## Restore

From the candidate repository, run:

```powershell
.\scripts\rollback_project_mcp.ps1 -ConfirmRestore
```

Then restart Codex. The script restores only the project config and project
instructions; it never modifies the user-global config.
