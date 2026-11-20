# Makes a worktree for a builder lane, with the shared dependency folders linked (never copied, never installed into).
# Usage: pwsh -File tools/work/new-lane.ps1 -Name <lane> -Branch <id> -From <base ref> [-Link "web/node_modules","api/.venv"]
# Removing a lane later: unlink first (Windows: cmd /c rmdir <link>; elsewhere: rm <link>), then `git worktree remove`.
param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string]$Branch, [string]$From = "main", [string[]]$Link = @())
$root = (git rev-parse --path-format=absolute --git-common-dir) -replace '[\\/]\.git$', ''
$wt = Join-Path (Split-Path $root) ((Split-Path $root -Leaf) + "-wt/$Name")
git -C $root worktree add $wt -b $Branch $From
if ($LASTEXITCODE -ne 0) { exit 1 }
foreach ($l in $Link) {
  $src = Join-Path $root $l; $dst = Join-Path $wt $l
  if (-not (Test-Path $src)) { Write-Warning "no $src to link"; continue }
  New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
  if ($IsWindows) { New-Item -ItemType Junction -Path $dst -Target $src | Out-Null } else { New-Item -ItemType SymbolicLink -Path $dst -Target $src | Out-Null }
}
"lane $Name at $wt on $Branch (from $From)"
