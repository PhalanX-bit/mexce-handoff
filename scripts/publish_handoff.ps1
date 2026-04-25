$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Branch = "codex/v2-stabilization"
$RemoteName = "origin"
$RemoteUrl = "https://github.com/PhalanX-bit/mexce-handoff.git"
$Message = if ($args.Count -gt 0) { $args -join " " } else { "Update handoff JSON files" }

Set-Location $RepoRoot

if (-not (Test-Path "docs")) {
    throw "Missing docs/ folder."
}

$ExistingRemote = $null
try {
    $ExistingRemote = git remote get-url $RemoteName 2>$null
} catch {
    $ExistingRemote = $null
}
if (-not $ExistingRemote) {
    git remote add $RemoteName $RemoteUrl
} elseif ($ExistingRemote -ne $RemoteUrl) {
    git remote set-url $RemoteName $RemoteUrl
}

git add docs scripts/publish_handoff.ps1

$Staged = git diff --cached --name-only
if (-not $Staged) {
    Write-Host "No handoff changes to publish."
    exit 0
}

git commit -m $Message
git branch -M $Branch
git push -u $RemoteName $Branch

Write-Host "Published handoff docs to $RemoteName/$Branch"
