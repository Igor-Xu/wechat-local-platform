[CmdletBinding()]
param(
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
Push-Location $project
try {
    $dirty = @(& git status --porcelain)
    if ($LASTEXITCODE -ne 0) { throw "git status failed" }
    if ($dirty.Count -ne 0) { throw "release requires a clean Git working tree" }

    $versionLine = Get-Content -LiteralPath (Join-Path $project "pyproject.toml") |
        Where-Object { $_ -match '^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"$' } |
        Select-Object -First 1
    if (-not $versionLine -or $versionLine -notmatch '^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"$') {
        throw "could not read a semantic version from pyproject.toml"
    }
    $version = $Matches[1]
    $tag = "v$version"
    $commit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$') { throw "could not resolve HEAD" }
    $tagCommit = (& git rev-list -n 1 $tag 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or $tagCommit -ne $commit) {
        throw "release tag $tag must exist and point to HEAD"
    }

    if (-not $OutputDirectory) {
        $OutputDirectory = Join-Path $project ".artifacts\releases\$tag"
    }
    $output = [IO.Path]::GetFullPath($OutputDirectory)
    New-Item -ItemType Directory -Force -Path $output | Out-Null

    $packageBase = "wechat-local-platform-$version"
    $archive = Join-Path $output "$packageBase.zip"
    $manifestPath = Join-Path $output "$packageBase.FILE_MANIFEST.json"
    $checksumPath = Join-Path $output "$packageBase.zip.sha256"

    & git archive --format=zip "--prefix=$packageBase/" "--output=$archive" $commit
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $archive)) {
        throw "git archive failed"
    }

    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("wechat-local-platform-release-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tempRoot | Out-Null
    try {
        Expand-Archive -LiteralPath $archive -DestinationPath $tempRoot
        $payloadRoot = Join-Path $tempRoot $packageBase
        $files = @(
            Get-ChildItem -LiteralPath $payloadRoot -Recurse -File |
                Sort-Object FullName |
                ForEach-Object {
                    $relative = $_.FullName.Substring($payloadRoot.Length + 1).Replace("\", "/")
                    [ordered]@{
                        path = $relative
                        bytes = $_.Length
                        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
                    }
                }
        )
    }
    finally {
        if (Test-Path -LiteralPath $tempRoot) {
            $resolvedTemp = (Resolve-Path -LiteralPath $tempRoot).Path
            $systemTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\")
            if (-not $resolvedTemp.StartsWith($systemTemp + "\", [StringComparison]::OrdinalIgnoreCase)) {
                throw "refusing to remove a temporary directory outside the system temp root"
            }
            Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
        }
    }

    $archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    $manifest = [ordered]@{
        schema_version = 1
        package = $packageBase
        version = $version
        tag = $tag
        git_commit = $commit
        generated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        archive = [ordered]@{
            filename = (Split-Path -Leaf $archive)
            bytes = (Get-Item -LiteralPath $archive).Length
            sha256 = $archiveHash
        }
        file_count = $files.Count
        files = $files
        exclusions = @(
            "machine-private configuration",
            "key maps and raw keys",
            "WeChat DB/WAL/SHM files",
            "snapshots, indexes and caches",
            "exports and local acceptance artifacts"
        )
    }
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    "$archiveHash  $(Split-Path -Leaf $archive)" | Set-Content -LiteralPath $checksumPath -Encoding ASCII

    [pscustomobject]@{
        ok = $true
        version = $version
        tag = $tag
        git_commit = $commit
        archive = $archive
        archive_sha256 = $archiveHash
        archive_bytes = (Get-Item -LiteralPath $archive).Length
        manifest = $manifestPath
        file_count = $files.Count
    } | ConvertTo-Json -Depth 4
}
finally {
    Pop-Location
}
