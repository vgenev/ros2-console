#!/usr/bin/env bash
# Run every admission check and print one PASS/FAIL row per check.
set -u
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

names=()
results=()

run() {
  local name="$1"
  shift
  echo "== ${name} =="
  if "$@"; then
    names+=("$name")
    results+=(PASS)
  else
    names+=("$name")
    results+=(FAIL)
  fi
  echo
}

run "offline pytest" python3 -m pytest tests -q -m 'not live'
run "node --check" bash -c 'cp desktop/plugin.js /tmp/pane-check.mjs && node --check /tmp/pane-check.mjs'
run "hermes plugins validate" hermes plugins validate "$root"
run "hermes plugins doctor" hermes plugins doctor "$root"
run "live pytest" env ROS2CONSOLE_LIVE=1 python3 -m pytest tests/test_live.py -q -m live

echo "gate                    result"
failed=0
for i in "${!names[@]}"; do
  printf '%-24s %s\n' "${names[$i]}" "${results[$i]}"
  if [[ "${results[$i]}" == FAIL ]]; then
    failed=1
  fi
done

exit "$failed"
