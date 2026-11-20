# Runs a command under a machine-wide lock, one caller at a time (shared test stacks, e2e runs, perf runs).
# Usage: pwsh -File tools/work/lock.ps1 -Name e2e -Who <batch> -Cmd "<powershell commands>" [-Dir <folder>] [-StaleMin 50]
# A lock older than -StaleMin minutes is taken as left by a dead session and removed.
# Put machine-wide defaults for every locked run (workers, GPU flags, reuse of a running stack) in tools/work/lock-env.ps1.
param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string]$Who, [Parameter(Mandatory)][string]$Cmd,
      [string]$Dir = (Get-Location).Path, [int]$StaleMin = 50)
$root = (git -C $PSScriptRoot rev-parse --path-format=absolute --git-common-dir) -replace '[\\/]\.git$', ''
$lock = Join-Path $root ".work/$Name.lock"
New-Item -ItemType Directory -Force (Split-Path $lock) | Out-Null
while ($true) {
  try { New-Item -ItemType File -Path $lock -Value "$Who $(Get-Date -Format o)" -ErrorAction Stop | Out-Null; break }
  catch {
    $held = Get-Item $lock -ErrorAction SilentlyContinue
    if (-not $held) { continue }
    if (((Get-Date) - $held.LastWriteTime).TotalMinutes -gt $StaleMin) { Remove-Item $lock -Force -ErrorAction SilentlyContinue; continue }
    Write-Host "$Name lock held by $(Get-Content $lock -ErrorAction SilentlyContinue); waiting"
    Start-Sleep -Seconds 10
  }
}
try {
  Set-Location $Dir
  $envFile = Join-Path $PSScriptRoot "lock-env.ps1"
  if (Test-Path $envFile) { . $envFile }
  Invoke-Expression $Cmd
} finally {
  Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
