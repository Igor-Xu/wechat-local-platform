[CmdletBinding()]
param(
    [switch]$ConfirmRestore
)

$ErrorActionPreference = "Stop"
if (-not $ConfirmRestore) {
    throw "Pass -ConfirmRestore to restore the pre-switch project configuration."
}

$project = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$workspace = (Resolve-Path (Join-Path $project "..\")).Path
$backupRoot = Join-Path $project ".artifacts\rollout-backups\20260814-wechat-local-platform-switch"
$configBackup = Join-Path $backupRoot "config.toml.before-switch"
$agentsBackup = Join-Path $backupRoot "AGENTS.md.before-switch"
$configTarget = Join-Path $workspace ".codex\config.toml"
$agentsTarget = Join-Path $workspace "AGENTS.md"

foreach ($path in @($configBackup, $agentsBackup)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "A required rollout backup is unavailable."
    }
}
foreach ($target in @($configTarget, $agentsTarget)) {
    $resolved = [IO.Path]::GetFullPath($target)
    if (-not $resolved.StartsWith($workspace + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "A restore target is outside the workspace."
    }
}

Copy-Item -LiteralPath $configBackup -Destination $configTarget -Force
Copy-Item -LiteralPath $agentsBackup -Destination $agentsTarget -Force

[pscustomobject]@{
    restored_project_config = $true
    restored_project_agents = $true
    global_config_modified = $false
    codex_restart_required = $true
} | Format-List
