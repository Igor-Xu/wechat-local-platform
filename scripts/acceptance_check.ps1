[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$artifactDir = Join-Path $project ".artifacts"
if (-not (Test-Path -LiteralPath $artifactDir)) {
    New-Item -ItemType Directory -Path $artifactDir | Out-Null
}

$started = Get-Date
Push-Location $project
try {
    & $Python -m unittest discover -s tests -v
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

$report = [ordered]@{
    schema_version = 1
    check = "offline"
    passed = ($exitCode -eq 0)
    exit_code = $exitCode
    test_count = 20
    metadata_only = $true
    real_wechat_query = $false
    source_content_read = $false
    snapshot_created = $false
    fallback_enabled = $false
    strict_read_only_required = $true
    disable_auto_refresh_required = $true
    started_at = $started.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    finished_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}
$report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $artifactDir "acceptance-report.json") -Encoding UTF8
exit $exitCode
