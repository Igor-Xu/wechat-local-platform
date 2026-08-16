# Threat model

## Protected assets

The protected assets are the encrypted WeChat database/WAL files, the verified
per-database key map, message bodies, account identifiers, local media paths,
and the ability to write local support data.

## Main threats and controls

| Threat | Control |
|---|---|
| A caller reaches arbitrary CLI commands | Static tool map; only `tools`, `tool-schema`, and allowlisted `call-json` are emitted by code |
| A child process changes mode | Adapter strips inherited switches and forces strict read-only plus auto-refresh disabled |
| A chat name selects the wrong conversation | In-process exact/unique-partial matching; ambiguous results fail closed |
| A message context silently uses the newest row | Context requires an explicit local/server anchor |
| Plaintext persists after a read | Adapter has no snapshot/index/cache/export/temp-file path |
| A failed direct read silently changes backend | There is no snapshot or fallback implementation |
| Keys or paths enter status/logs | Status walker and error conversion allow only metadata categories; stderr is never returned |
| A large query exhausts the model/process | Public limits, bounded resolver pages and upstream output cap |
| A future update changes the binary | Hash-locked runtime and immutable v1.6.20 provenance |

## Accepted exposure

When the user asks to inspect a conversation, successful responses may contain
bounded body text, normal upstream message identifiers/cursors and existing
readable media paths. These values exist only in the current response and are
not written by the adapter. This is different from returning keys, raw database
pages, private configuration paths or debug parser state.

## Residual risks

The pinned upstream binary is not Authenticode-signed and libWCDB provenance is
limited by the upstream release evidence. Automatic key maintenance and future
Weixin-version compatibility are not solved by this Beta. The accepted live
run is machine-specific; a new host or materially changed runtime/data layout
requires another live acceptance before its production use.
