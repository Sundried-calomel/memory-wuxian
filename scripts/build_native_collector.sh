#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CARGO_BIN="${CARGO_BIN:-$HOME/.cargo/bin/cargo}"
MANIFEST="$SKILL_ROOT/native-collector/Cargo.toml"
OUTPUT_DIR="$SKILL_ROOT/bin"
OUTPUT="$OUTPUT_DIR/memory-wuxian-collector"

if [[ ! -x "$CARGO_BIN" ]]; then
  printf 'Cargo executable not found: %s\n' "$CARGO_BIN" >&2
  exit 1
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<EOF
Build the optimized Memory Wuxian native collector.

Usage:
  scripts/build_native_collector.sh

Environment:
  CARGO_BIN  Cargo executable (default: \$HOME/.cargo/bin/cargo)

Output:
  $OUTPUT

This command compiles local source and does not load or modify the LaunchAgent.
EOF
  exit 0
fi

"$CARGO_BIN" build --locked --release --manifest-path "$MANIFEST"
mkdir -p "$OUTPUT_DIR"
install -m 0755 "$SKILL_ROOT/native-collector/target/release/memory-wuxian-collector" "$OUTPUT"
if [[ "$(uname -s)" == "Darwin" ]]; then
  /usr/bin/codesign --force --sign - --identifier com.memorywuxian.collector "$OUTPUT"
fi
printf '%s\n' "$OUTPUT"
