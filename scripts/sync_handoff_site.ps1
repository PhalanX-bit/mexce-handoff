$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Docs = Join-Path $Root "docs"

New-Item -ItemType Directory -Force -Path $Docs | Out-Null

$Files = @(
    "project_status.json",
    "next_task.json",
    "last_result.json",
    "task_history.json"
)

foreach ($File in $Files) {
    Copy-Item -LiteralPath (Join-Path $Root $File) -Destination (Join-Path $Docs $File) -Force
}

Write-Host "Synced handoff JSON files to docs/"
