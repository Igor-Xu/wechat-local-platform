# Live acceptance

## Prerequisite

The user must manually exit Weixin and keep it closed. Both `Weixin.exe` and
`WeChat.exe` process counts must be zero. A nonzero count stops the harness
before it opens the machine-private config, hashes a database or starts the
production adapter.

## One-run sequence

1. Validate the private config and locked Python/EXE/DLL identities.
2. Enumerate the configured encrypted `db_storage` root without following
   symlinks.
3. Hash every original DB and WAL; hash SHM separately.
4. Hash the managed key map, private config, runtime files and managed state
   tree.
5. Start the stdio MCP and require the exact 18-tool inventory.
6. Call metadata-only `status` and require direct/live/strict/no-fallback gates.
7. Exercise sessions, contacts, unread, resolver behavior, timeline, exact
   context, known-hit search, search context, events, group members, media,
   favorites, Moments, transfers and red packets.
8. Destroy all successful payloads with process exit; no payload is written.
9. Re-hash DB/WAL/SHM and every protected local input.
10. Write only the metadata acceptance report.

The report includes tool/scenario names, field names, counts, booleans, elapsed
milliseconds and change counts. It excludes request keywords, labels, account
or chat identifiers, message bodies, keys, filenames, hashes and private paths.

## Fail-closed conditions

Any of the following makes the run fail:

- a Weixin process exists before or after the run;
- locked runtime validation fails;
- the tool inventory differs from the production allowlist;
- any status safety gate is false;
- an ordinary tool call fails or required live anchor cannot be found;
- DB, WAL, managed state, key map, config or runtime changes;
- the adapter times out, exits or emits invalid JSON.

SHM is disclosed separately. A SHM difference does not get relabeled as a DB or
WAL write, but remains visible in the report for review.
