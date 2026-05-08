#!/usr/bin/env bash
# run.sh — runner wrapper for ralphify ralphs.
#
# Adds three guards on top of `ralph run`:
#   1. Refuses to start unless `-n` / `--max-iterations` is supplied.
#   2. Surfaces each iteration boundary in the live output.
#   3. Watches for `<promise>COMPLETE</promise>`; exits 0 if seen,
#      non-zero with a banner if the cap is hit without it.
#
# Usage: run.sh PATH/TO/RALPH_DIR -n 50 [other ralph run flags...]

set -euo pipefail

SCRIPT_NAME="$(basename "$0")"

die() {
  printf '%s: %s\n' "$SCRIPT_NAME" "$*" >&2
  exit 2
}

if [ "$#" -lt 1 ]; then
  die "usage: $SCRIPT_NAME PATH/TO/RALPH_DIR [-n N] [ralph run flags...]"
fi

# Find the cap (-n / --max-iterations) without consuming positional args we
# need to forward verbatim. Walk the argv once.
cap=""
prev=""
for arg in "$@"; do
  case "$prev" in
    -n|--max-iterations)
      cap="$arg"
      ;;
  esac
  case "$arg" in
    --max-iterations=*)
      cap="${arg#--max-iterations=}"
      ;;
    -n=*)
      cap="${arg#-n=}"
      ;;
  esac
  prev="$arg"
done

if [ -z "$cap" ]; then
  cat >&2 <<'EOF'
run.sh: refusing to start — no iteration cap supplied.

Pass `-n N` (or `--max-iterations N`) to bound the loop. Unbounded ralphs
burn tokens until the agent crashes or the user notices.

Recommended first run: -n 50 -t 1800 -s
EOF
  exit 2
fi

case "$cap" in
  *[!0-9]*|"")
    die "iteration cap must be a positive integer (got '$cap')"
    ;;
esac

if [ "$cap" -lt 1 ]; then
  die "iteration cap must be >= 1 (got '$cap')"
fi

if ! command -v ralph >/dev/null 2>&1; then
  die "ralph binary not on PATH — install ralphify (uv tool install ralphify)"
fi

export RALPH_CAP="$cap"

complete_marker='<promise>COMPLETE</promise>'

# Stream `ralph run` output to a temp log AND to the terminal so a human
# can watch in real time, while we scan for the COMPLETE marker.
log="$(mktemp -t ralph-run.XXXXXX)"
trap 'rm -f "$log"' EXIT

printf '>>> ralph run with cap=%s\n' "$cap" >&2

set +e
ralph run "$@" 2>&1 | tee "$log"
ralph_status="${PIPESTATUS[0]}"
set -e

if grep -Fq "$complete_marker" "$log"; then
  printf '>>> COMPLETE sentinel observed — loop terminated cleanly.\n' >&2
  exit 0
fi

if [ "$ralph_status" -ne 0 ]; then
  printf '>>> ralph run exited %s without COMPLETE — see log above.\n' \
    "$ralph_status" >&2
  exit "$ralph_status"
fi

cat >&2 <<EOF
>>> CAP HIT WITHOUT COMPLETE
The loop ran the full $cap iterations without the agent emitting
$complete_marker. Treat this as a failure — inspect the logs and decide
whether to raise the cap, fix the agent's exit logic, or abandon the run.
EOF
exit 1
