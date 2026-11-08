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
SIGN_ID="${APPLE_SIGNING_IDENTITY:-Apple Development: contact@marqueslucio.com (ULTQS3R2AQ)}"
ENTITLEMENTS="$ROOT_DIR/tauri/src-tauri/Entitlements.plist"

codesign --force --sign "$SIGN_ID" --entitlements "$ENTITLEMENTS" \
  "$SOURCE_APP/Contents/MacOS/voicebox-server"
codesign --force --sign "$SIGN_ID" --entitlements "$ENTITLEMENTS" "$SOURCE_APP"
codesign --verify --deep --strict "$SOURCE_APP"
signature_details="$(codesign -dv "$SOURCE_APP" 2>&1)"
if ! grep -Fxq 'TeamIdentifier=469AG7ZM8L' <<< "$signature_details"; then
  echo "App must be signed with the Voicebox development team identity." >&2
  exit 1
fi

if pgrep -f "$DEST_APP/Contents/MacOS/voicebox" >/dev/null; then
  echo "Quit Voicebox before installing a new build." >&2
  exit 1
fi
if [[ -e "$DEST_APP" ]]; then
  old_requirement="$(codesign -dr - "$DEST_APP" 2>&1 | sed -n 's/^designated => //p')"
  new_requirement="$(codesign -dr - "$SOURCE_APP" 2>&1 | sed -n 's/^designated => //p')"
  if [[ -z "$old_requirement" || "$old_requirement" != "$new_requirement" ]]; then
    echo "The signed app's designated requirement differs from the installed app." >&2
    exit 1
  fi
  rm -rf "$DEST_APP"
fi

mv "$SOURCE_APP" "$DEST_APP"
codesign --verify --deep --strict "$DEST_APP"
echo "Installed Voicebox $(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$DEST_APP/Contents/Info.plist") at $DEST_APP"
