# Runs a command (usually e2e files) for this worktree's COMMITTED HEAD on the second machine, under that machine's lock.
# Usage (in the foreground with a long tool timeout; read the log's tail once, no polling):
#   pwsh -File tools/work/remote-run.ps1 -Id <batch> -Cmd "<bash: bring the stack up && run the named files>" [-Log .work/remote-<id>.log]
# It pushes HEAD to the remote clone as refs/heads/remote/<id>, checks it out in <repo>-wt-remote-<id> there,
# links the dependency folders listed in -Link to the clone's own, and runs -Cmd through tools/work/lock.sh e2e.
param([Parameter(Mandatory)][string]$Id, [Parameter(Mandatory)][string]$Cmd,
      [string]$RemoteHost = "{{REMOTE_HOST}}", [string]$RemotePath = "{{REMOTE_PATH}}",
      [string[]]$Link = @(), [string]$Log = ".work/remote-$Id.log")
$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Force (Split-Path $Log) | Out-Null
"start $(Get-Date -Format o)" | Set-Content $Log
if (git status --porcelain | Where-Object { $_ -notmatch '\.serena' }) { "WARN: uncommitted changes are NOT tested" | Add-Content $Log }
$sha = git rev-parse HEAD
# git's own ssh has no timeout: bound it like the ssh below (an owner's GIT_SSH_COMMAND is kept)
if (-not $env:GIT_SSH_COMMAND) { $env:GIT_SSH_COMMAND = "ssh -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30 -o ServerAliveCountMax=4" }
git push -q -f "${RemoteHost}:$RemotePath" "HEAD:refs/heads/remote/$Id" *>> $Log
if ($LASTEXITCODE -ne 0) { "FAILED: push to $RemoteHost" | Add-Content $Log; exit 1 }
$links = ($Link | ForEach-Object { "[ -e `"`$wt/$_`" ] || ln -s `"$RemotePath/$_`" `"`$wt/$_`"" }) -join "`n"
$cmdQ = $Cmd -replace "'", "'\''"
$sh = @"
set -u
[ -d `$HOME/.docker-ssh ] && export DOCKER_CONFIG=`$HOME/.docker-ssh   # ssh sessions on macOS cannot open the keychain
wt="${RemotePath}-wt-remote-$Id"
[ -d "`$wt" ] || git -C "$RemotePath" worktree add -q --detach "`$wt" "remote/$Id"
git -C "`$wt" checkout -q --detach -f $sha || exit 3
$links
echo "testing $sha in `$wt"
bash "`$wt/tools/work/lock.sh" e2e "remote-$Id" "`$wt" '$cmdQ'
echo "exit `$?"
"@
($sh -replace "`r", "") | ssh -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30 $RemoteHost bash -s *>> $Log
"done $(Get-Date -Format o)" | Add-Content $Log
