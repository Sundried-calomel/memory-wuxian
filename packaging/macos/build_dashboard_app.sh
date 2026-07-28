#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
version="${1:?usage: build_dashboard_app.sh VERSION OUTPUT_APP [native|universal]}"
output_app="${2:?usage: build_dashboard_app.sh VERSION OUTPUT_APP [native|universal]}"
mode="${3:-universal}"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
export CLANG_MODULE_CACHE_PATH="$work_dir/clang-module-cache"
export SWIFT_MODULECACHE_PATH="$work_dir/swift-module-cache"

rm -rf "$output_app"
mkdir -p "$output_app/Contents/MacOS" "$output_app/Contents/Resources"

build_arch() {
  local architecture="$1"
  xcrun swiftc \
    -O \
    -target "$architecture-apple-macos13.0" \
    -framework AppKit \
    -framework Foundation \
    -framework WebKit \
    "$repo_root/packaging/macos/MemoryDashboard.swift" \
    -o "$work_dir/MemoryDashboard-$architecture"
}

if [[ "$mode" == "universal" ]]; then
  build_arch arm64
  build_arch x86_64
  lipo -create \
    "$work_dir/MemoryDashboard-arm64" \
    "$work_dir/MemoryDashboard-x86_64" \
    -output "$output_app/Contents/MacOS/MemoryDashboard"
elif [[ "$mode" == "native" ]]; then
  architecture="$(uname -m)"
  build_arch "$architecture"
  cp "$work_dir/MemoryDashboard-$architecture" \
    "$output_app/Contents/MacOS/MemoryDashboard"
else
  echo "Unsupported dashboard architecture mode: $mode" >&2
  exit 1
fi

cat > "$output_app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>zh_CN</string>
  <key>CFBundleDisplayName</key><string>Memory無限操作台</string>
  <key>CFBundleExecutable</key><string>MemoryDashboard</string>
  <key>CFBundleIconFile</key><string>MemoryWuxian</string>
  <key>CFBundleIdentifier</key><string>io.github.sundried-calomel.memory-wuxian.dashboard</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>Memory無限操作台</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>CFBundleVersion</key><string>$version</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
</dict>
</plist>
PLIST

cp "$repo_root/assets/memory-wuxian.icns" \
  "$output_app/Contents/Resources/MemoryWuxian.icns"

chmod +x "$output_app/Contents/MacOS/MemoryDashboard"
codesign --force --deep --sign - "$output_app"
codesign --verify --deep --strict "$output_app"
actual_version="$(/usr/libexec/PlistBuddy \
  -c 'Print :CFBundleShortVersionString' \
  "$output_app/Contents/Info.plist")"
[[ "$actual_version" == "$version" ]]
