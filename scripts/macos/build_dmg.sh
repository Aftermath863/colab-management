#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
APP_NAME="Colab Manager"
APP_DIR="$ROOT_DIR/dist/${APP_NAME}.app"
DMG_PATH="$ROOT_DIR/dist/Colab-Manager-macOS-arm64.dmg"

if [[ ! -d "$APP_DIR" ]]; then
    echo "==> ${APP_NAME}.app not found; running build_app.sh..."
    "$SCRIPT_DIR/build_app.sh"
fi

STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT

echo "==> Staging ${APP_NAME}.app and /Applications symlink..."
ditto "$APP_DIR" "$STAGE_DIR/${APP_NAME}.app"
ln -s /Applications "$STAGE_DIR/Applications"

mkdir -p "$ROOT_DIR/dist"
echo "==> Creating compressed disk image: $DMG_PATH"
hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$STAGE_DIR" \
    -ov \
    -format UDZO \
    "$DMG_PATH"

if [[ ! -f "$DMG_PATH" ]]; then
    echo "Error: DMG creation failed at $DMG_PATH" >&2
    exit 1
fi

echo "==> Successfully created: $DMG_PATH"
ls -lh "$DMG_PATH"
shasum -a 256 "$DMG_PATH"
