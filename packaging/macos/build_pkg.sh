#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
version="${1:?usage: build_pkg.sh VERSION [OUTPUT_DIRECTORY]}"
output_dir="${2:-$repo_root/dist}"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

package_version="$version"
if [[ "$version" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)b([0-9]+)$ ]]; then
  package_version="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.$((10#${BASH_REMATCH[3]} * 100 + 10#${BASH_REMATCH[4]}))"
fi

collector="$repo_root/bin/memory-wuxian-collector"
envelope="$repo_root/bin/memory-wuxian-envelope"
for binary in "$collector" "$envelope"; do
  if [[ ! -x "$binary" ]]; then
    echo "Missing executable native binary: $binary" >&2
    exit 1
  fi
  architectures="$(lipo -archs "$binary")"
  if [[ "$architectures" != *"arm64"* || "$architectures" != *"x86_64"* ]]; then
    echo "macOS release binary must be universal (arm64 and x86_64): $binary: $architectures" >&2
    exit 1
  fi
done

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

package_python="${PYTHON_EXECUTABLE:-python3}"
yaml_source="$($package_python -c 'from pathlib import Path; import yaml; print(Path(yaml.__file__).resolve().parent)')"
if [[ ! -f "$yaml_source/__init__.py" ]]; then
  echo "PyYAML source package is unavailable for the offline macOS runtime." >&2
  exit 1
fi
mkdir -p "$payload_skill/vendor"
rsync -a \
  --exclude __pycache__/ \
  --exclude '*.pyc' \
  "$yaml_source/" "$payload_skill/vendor/yaml/"

dashboard_app="$payload_skill/assets/macos/Memory無限操作台.app"
"$repo_root/packaging/macos/build_dashboard_app.sh" \
  "$version" "$dashboard_app" universal
"$repo_root/scripts/install_dashboard_app_macos.py" --help >/dev/null
dashboard_version="$(/usr/libexec/PlistBuddy \
  -c 'Print :MemoryWuxianProductVersion' \
  "$dashboard_app/Contents/Info.plist")"
if [[ "$dashboard_version" != "$version" ]]; then
  echo "macOS dashboard version does not match package version." >&2
  exit 1
fi

component_plist="$work_dir/component.plist"
pkgbuild --analyze --root "$work_dir/root" "$component_plist"
dashboard_bundle_path="Library/Application Support/MemoryWuxian/skill/assets/macos/Memory無限操作台.app"
component_bundle_path="$(/usr/libexec/PlistBuddy \
  -c 'Print :0:RootRelativeBundlePath' "$component_plist" 2>/dev/null || true)"
if [[ "$component_bundle_path" != "$dashboard_bundle_path" ]]; then
  echo "Unexpected macOS package bundle path: $component_bundle_path" >&2
  exit 1
fi
/usr/libexec/PlistBuddy \
  -c 'Set :0:BundleIsRelocatable false' "$component_plist"
if [[ "$(/usr/libexec/PlistBuddy -c 'Print :0:BundleIsRelocatable' "$component_plist")" != "false" ]]; then
  echo "macOS dashboard bundle must be marked non-relocatable." >&2
  exit 1
fi

pkgbuild \
  --root "$work_dir/root" \
  --scripts "$repo_root/packaging/macos/scripts" \
  --component-plist "$component_plist" \
  --identifier "io.github.sundried-calomel.memory-wuxian" \
  --version "$package_version" \
  --install-location / \
  "$output_dir/MemoryWuxian-$version-macOS-universal.pkg"

(
  cd "$output_dir"
  shasum -a 256 "MemoryWuxian-$version-macOS-universal.pkg" \
    > "MemoryWuxian-$version-macOS-universal.pkg.sha256"
)
