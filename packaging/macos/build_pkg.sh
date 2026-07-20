#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
version="${1:?usage: build_pkg.sh VERSION [OUTPUT_DIRECTORY]}"
output_dir="${2:-$repo_root/dist}"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

collector="$repo_root/bin/memory-wuxian-collector"
if [[ ! -x "$collector" ]]; then
  echo "Missing executable collector: $collector" >&2
  exit 1
fi
architectures="$(lipo -archs "$collector")"
if [[ "$architectures" != *"arm64"* || "$architectures" != *"x86_64"* ]]; then
  echo "macOS release collector must be universal (arm64 and x86_64): $architectures" >&2
  exit 1
fi

payload_skill="$work_dir/root/Library/Application Support/MemoryWuxian/skill"
mkdir -p "$payload_skill" "$output_dir"
rsync -a \
  --exclude .git/ \
  --exclude .github/ \
  --exclude memory/ \
  --exclude native-collector/target/ \
  --exclude packaging/ \
  --exclude dist/ \
  --exclude outputs/ \
  --exclude __pycache__/ \
  --exclude '*.pyc' \
  "$repo_root/" "$payload_skill/"

pkgbuild \
  --root "$work_dir/root" \
  --scripts "$repo_root/packaging/macos/scripts" \
  --identifier "io.github.sundried-calomel.memory-wuxian" \
  --version "$version" \
  --install-location / \
  "$output_dir/MemoryWuxian-$version-macOS-universal.pkg"

shasum -a 256 "$output_dir/MemoryWuxian-$version-macOS-universal.pkg" \
  > "$output_dir/MemoryWuxian-$version-macOS-universal.pkg.sha256"
