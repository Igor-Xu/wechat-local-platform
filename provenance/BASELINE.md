# Locked upstream baseline

This repository starts from the locally verified `r266-tech/wechat-cli` v1.6.20
release and Go module source. No network download was used to construct this
baseline.

## Source identity

- Module: `github.com/r266-tech/wechat-cli`
- Version: `v1.6.20`
- Origin commit: `794541d13f8697436919f8c63aea586e3f4291d8`
- Go module sum: `h1:bAlae5jkHU7aKRvtFmvKs4efsBp62Y+6/0H394J1y/g=`
- Source tree location: `vendor/wechat-cli-v1.6.20`
- Module archive: `provenance/v1.6.20.module.zip`

## Locked Windows amd64 runtime

- Release archive SHA-256: `0b0ba998b1d86209e37310f1166c381d13e9a1978fbcd34f4cc5ccc5cbd8d10b`
- `wechat-cli.exe` SHA-256: `1ad112c4ed10e05757c685698a20d181ab0d75ae3dde3d076895cc6947ae91ed`
- `libWCDB.dll` SHA-256: `beefb9ea3822468116eb86ff49bae6c34e7811916c4c761acef31ec3952da360`
- Runtime location: `runtime/windows-amd64`

The complete pre-existing audit lock is retained in `sources.lock.json`. This
baseline contains no account data, database, WAL, key map, raw key, snapshot,
cache, export, or machine-private configuration.

## Update rule

v1.6.21 and later versions are comparison inputs only. Changing the baseline
requires a new provenance review, explicit version update, fresh hashes, and
regression acceptance; it must never happen through `latest` or an updater.
