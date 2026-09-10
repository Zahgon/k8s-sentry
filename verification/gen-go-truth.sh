#!/usr/bin/env bash
# Record the Go truth tables from the real wichert/k8s-sentry checkout.
#
#   ./gen-go-truth.sh [/path/to/wichert_k8s-sentry]
#
# The checkout is read-only: the probe is copied into a throwaway $TMPDIR copy,
# executed there, and the script fails if `git status` in the original changes.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GO_REPO="${1:-$HERE/../../../source repo/wichert_k8s-sentry}"

if [ ! -f "$GO_REPO/application.go" ]; then
  echo "not a wichert/k8s-sentry checkout: $GO_REPO" >&2
  exit 1
fi

BEFORE="$(cd "$GO_REPO" && git status --porcelain)"

python3 "$HERE/truth_inputs.py"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

tar -C "$GO_REPO" -cf - --exclude .git . | tar -C "$WORK" -xf -
cp "$HERE/go-probe.go.txt" "$WORK/probe_test.go"

pushd "$WORK" >/dev/null
K8SSENTRY_TRUTH_IN="$HERE/truth-inputs.json" K8SSENTRY_TRUTH_OUT="$HERE/go-truth.json" \
  go test -run TestGoTruthProbe -count=1 .
popd >/dev/null

AFTER="$(cd "$GO_REPO" && git status --porcelain)"
if [ "$BEFORE" != "$AFTER" ]; then
  echo "FAIL: the Go checkout was modified" >&2
  exit 1
fi

echo "go-truth.json recorded; Go checkout is clean"
