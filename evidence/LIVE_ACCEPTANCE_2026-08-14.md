# Live acceptance — 2026-08-14

Candidate commit under test: `3c498c0`. The run used the existing
machine-private configuration only as an input; it was not copied into this
repository and the Codex MCP configuration was not changed.

## Safety gates

| Gate | Result |
|---|---:|
| Weixin/WeChat process count before | 0 |
| Weixin/WeChat process count after | 0 |
| backend direct | true |
| live read ok | true |
| metadata-only status | true |
| strict read-only | true |
| auto refresh disabled | true |
| fallback enabled | false |
| snapshot used | false |
| payload persisted by harness/adapter | false |
| exact candidate tool count | 18 |
| ordinary tool failures | 0 |

## Real calls

All 18 candidate tools were enumerated and called successfully. The run also
completed exact, unique partial and ambiguous chat-resolution scenarios, exact
message context, a known-hit search and search-with-context.

- Chat timeline: 50 rows.
- Message context: 6 rows around an explicit anchor.
- Known-hit search: 10 hits.
- Moments feed: 10 posts.
- Moments search: 1 hit from a live Moments-content probe.
- Media resources: 200 bounded records; 5 existing readable local paths in
  the final sample. The first chat-anchored probe was empty and did not cause
  a cache or decode fallback.

## Immutability

| Asset | Before | After | Content changes | mtime changes | Result |
|---|---:|---:|---:|---:|---|
| Original DB | 26 | 26 | 0 | 0 | unchanged |
| Original WAL | 26 | 26 | 0 | 0 | unchanged |
| SHM | 26 | 26 | 0 | 16 | disclosed separately |
| Managed state files | 0 | 0 | 0 | 0 | unchanged |
| Config/key map/runtime | — | — | 0 | — | unchanged |

`original_db_wal_written_by_access=false`.

The SHM timestamp changes are not reported as DB/WAL writes. No snapshot,
plaintext search index, body cache, export or fallback was created or used.
The report itself is metadata-only and contains no message body, chat label,
account identifier, key, filename, hash or private path.
