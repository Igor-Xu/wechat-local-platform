# Upstream audit record

## Locked input

The baseline is the locally archived `github.com/r266-tech/wechat-cli`
`v1.6.20` module and its Windows amd64 release asset. The archived module
reports origin commit `794541d13f8697436919f8c63aea586e3f4291d8`; its module
sum is recorded in `v1.6.20.sumdb.txt` and matches the independently recorded
module hash in `BASELINE.md`.

The locally recomputed runtime hashes are fixed in `sources.lock.json` and
covered by automated tests:

- `wechat-cli.exe`: `1ad112c4ed10e05757c685698a20d181ab0d75ae3dde3d076895cc6947ae91ed`
- `libWCDB.dll`: `beefb9ea3822468116eb86ff49bae6c34e7811916c4c761acef31ec3952da360`

## Audit limits

- The release executable and DLL are not Authenticode-signed.
- No silent update, `latest`, installer execution or network download is part
  of this project.
- v1.6.21 is retained outside this repository as a comparison input only.
- Windows Weixin-version compatibility and any future key-acquisition work
  require a separate live evidence run.

## Adapter attestation

Before the first read, the adapter calls the pinned binary's static `tools`
catalog and `tool-schema` for every mapped upstream read tool. Each required
record must state `read_only=true`, no required local write, and a strict
read-only behavior of `same` or `allowed_without_writes`. A missing or
contradictory record stops the process.
