#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_APP="$ROOT_DIR/tauri/src-tauri/target/release/bundle/macos/Voicebox.app"
DEST_APP="/Applications/Voicebox.app"
MODE="${1:-build}"

case "$MODE" in
  build|--install-built) ;;
  *)
    echo "usage: $0 [build|--install-built]" >&2
    exit 2
    ;;
esac

if pgrep -f "$DEST_APP/Contents/MacOS/voicebox" >/dev/null; then
  echo "Quit Voicebox before installing a new build." >&2
  exit 1
fi

if [[ "$MODE" == build ]]; then
  cd "$ROOT_DIR"
  ./scripts/build-server.sh
  cd "$ROOT_DIR/tauri"
  bun run tauri build --bundles app --config '{"bundle":{"createUpdaterArtifacts":false}}'
fi

[[ -d "$SOURCE_APP" ]] || {
  echo "Built app not found: $SOURCE_APP" >&2
  exit 1
}

# macOS keys the Input Monitoring grant (kTCCServiceListenEvent) to the
# signature's designated requirement. An ad-hoc signature has none and its
# cdhash changes every build, so TCC drops the grant and the app never shows
# up under Privacy & Security. Sign with a real identity to keep it stable.
SIGN_ID="${APPLE_SIGNING_IDENTITY:--}"
ENTITLEMENTS="$ROOT_DIR/tauri/src-tauri/Entitlements.plist"

codesign --force --sign "$SIGN_ID" --entitlements "$ENTITLEMENTS" \
  "$SOURCE_APP/Contents/MacOS/voicebox-server" \
  "$SOURCE_APP/Contents/MacOS/voicebox-mcp"
codesign --force --sign "$SIGN_ID" --entitlements "$ENTITLEMENTS" "$SOURCE_APP"
codesign --verify --deep --strict "$SOURCE_APP"

if [[ -e "$DEST_APP" ]]; then
  backup="$HOME/.Trash/Voicebox-$(date +%Y%m%d-%H%M%S).app"
  mv "$DEST_APP" "$backup"
  echo "Previous app moved to: $backup"
fi

ditto "$SOURCE_APP" "$DEST_APP"
echo "Installed Voicebox $(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$DEST_APP/Contents/Info.plist") at $DEST_APP"
