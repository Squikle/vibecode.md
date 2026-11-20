#!/bin/bash
# Runs a command under a machine-wide lock, one caller at a time (shared test stacks, e2e runs, perf runs).
# Usage: tools/work/lock.sh <name> <who> <dir> '<bash commands>'   (STALE_MIN=50 by default)
# Machine-wide defaults for every locked run (workers, node version, DOCKER_CONFIG) go in tools/work/lock-env.sh.
name="$1"; who="$2"; dir="$3"; cmd="$4"
here="$(cd "$(dirname "$0")" && pwd)"
root="$(git -C "$here" rev-parse --path-format=absolute --git-common-dir | sed 's#/\.git$##')"
lock="$root/.work/$name.lock.d"
mkdir -p "$root/.work"
while ! mkdir "$lock" 2>/dev/null; do
  if [ -n "$(find "$lock" -maxdepth 0 -mmin +"${STALE_MIN:-50}" 2>/dev/null)" ]; then rm -rf "$lock"; continue; fi
  echo "$name lock held by $(cat "$lock/who" 2>/dev/null); waiting"; sleep 10
done
echo "$who $(date)" > "$lock/who"
trap 'rm -rf "$lock"' EXIT INT TERM
[ -f "$here/lock-env.sh" ] && . "$here/lock-env.sh"
cd "$dir" && bash -c "$cmd"
