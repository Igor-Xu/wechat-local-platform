# Capability matrix

| Candidate tool | Upstream source | Normal response policy | Persistent local write |
|---|---|---|---|
| status | read_os | metadata-only, redacted | no |
| sessions / unread | sessions / unread | upstream structured payload | no; unread may report unavailable if its existing metadata capability is absent |
| resolve_chat | direct sessions + contacts | in-process redacted resolution | no |
| chat_timeline | chat_timeline | upstream structured messages | no |
| message_context | message_context | exact anchored upstream context | no |
| search / search_with_context | search / search_with_context | upstream live FTS and bounded context | no |
| read_events | read_events | bounded event payload/cursor | no |
| contacts / group_members | contacts / group_members | upstream structured rows | no |
| media_resources | media_resources | existing readable media paths may return | no cache creation |
| favorites | favorites | structured favorites | no |
| moments_feed / moments_search | sns_feed / sns_search | structured Moments feed/search | no |
| moments_notifications | sns_notifications | structured notifications | no |
| transfers / red_packets | transfers / red_packets | structured payment records | no |

The upstream catalog must attest each mapped operation as `read_only=true`,
with strict-read-only behavior equal to `same` or
`allowed_without_writes`. A missing or contradictory attestation stops the
adapter before any content call.

The 2026-08-14 candidate live run verified the mapped read tools, resolver
scenarios, Moments feed/search and media resource lookup. The DB/WAL
immutability and metadata-only report are retained under `evidence/`.
Production use still requires a new-Codex-task check and separate
configuration-switch approval.
