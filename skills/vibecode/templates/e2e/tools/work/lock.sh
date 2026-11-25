#!/bin/bash
# Runs a command under a machine-wide lock, one caller at a time (shared test stacks, e2e runs, perf runs).
# Usage: tools/work/lock.sh <name> <who> <dir> '<bash commands>'   (STALE_MIN=50 by default)
# HELD=1 (or CI_LOCK_HELD naming this lock: the gate took it, tools/ci/gate.py): the caller holds the lock already,
# so this run neither waits for it nor frees it.
# Machine-wide defaults for every locked run (workers, node version, DOCKER_CONFIG) go in tools/work/lock-env.sh.
name="$1"; who="$2"; dir="$3"; cmd="$4"
here="$(cd "$(dirname "$0")" && pwd)"
root="$(git -C "$here" rev-parse --path-format=absolute --git-common-dir | sed 's#/\.git$##')"
lock="$root/.work/$name.lock.d"
mkdir -p "$root/.work"
if [ -z "$HELD" ] && [ "$CI_LOCK_HELD" != "$name" ]; then
  seen=
  while ! mkdir "$lock" 2>/dev/null; do
    if [ -n "$(find "$lock" -maxdepth 0 -mmin +"${STALE_MIN:-50}" 2>/dev/null)" ]; then rm -rf "$lock"; continue; fi
    by="$(cat "$lock/who" 2>/dev/null)"
    if [ "$by" != "$seen" ]; then echo "$name lock held by $by; waiting"; seen="$by"; fi  # once per holder
    sleep 10
  done
  echo "$who $(date +%Y-%m-%dT%H:%M:%S)" > "$lock/who"
  trap 'rm -rf "$lock"' EXIT INT TERM
fi
[ -f "$here/lock-env.sh" ] && . "$here/lock-env.sh"
cd "$dir" && bash -c "$cmd"
