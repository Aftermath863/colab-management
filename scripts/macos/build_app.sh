#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
APP_NAME="Colab Manager"
EXECUTABLE_NAME="ColabManager"
APP_DIR="$ROOT_DIR/dist/${APP_NAME}.app"
SKIP_FRONTEND_BUILD="${SKIP_FRONTEND_BUILD:-0}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "==> Packaging ${APP_NAME}.app (Apple Silicon arm64)"
echo "    Repo root: $ROOT_DIR"

# 1. Build frontend production bundle unless SKIP_FRONTEND_BUILD=1
if [[ "$SKIP_FRONTEND_BUILD" != "1" ]]; then
    echo "==> [1/6] Building frontend bundle..."
    if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
        echo "    node_modules missing; running npm ci..."
        (cd "$ROOT_DIR/frontend" && npm ci --no-audit --no-fund)
    fi
    (cd "$ROOT_DIR/frontend" && npm run build)
else
    echo "==> [1/6] Skipping frontend build (SKIP_FRONTEND_BUILD=1)"
fi

if [[ ! -f "$ROOT_DIR/frontend/dist/index.html" ]]; then
    echo "Error: frontend build output missing at $ROOT_DIR/frontend/dist/index.html" >&2
    exit 1
fi

# 2. Generate AppIcon.icns
echo "==> [2/6] Generating macOS squircle icon (AppIcon.icns)..."
ICON_PATH="$TMP_DIR/AppIcon.icns"
swift "$ROOT_DIR/scripts/macos/generate_icon.swift" "$ICON_PATH"

# 3. Build native Swift arm64 binary
echo "==> [3/6] Building native macOS Swift shell (release, arm64)..."
if [[ ! -f "$ROOT_DIR/macos/Package.swift" ]]; then
    echo "Error: native macOS package not found at $ROOT_DIR/macos/Package.swift" >&2
    exit 1
fi

(cd "$ROOT_DIR/macos" && swift build -c release --arch arm64)
BIN_PATH="$(cd "$ROOT_DIR/macos" && swift build -c release --arch arm64 --show-bin-path)"
BUILT_BINARY="$BIN_PATH/$EXECUTABLE_NAME"

if [[ ! -x "$BUILT_BINARY" ]]; then
    echo "Error: compiled binary not found or not executable at $BUILT_BINARY" >&2
    exit 1
fi

# 4. Assemble .app bundle
echo "==> [4/6] Assembling bundle at $APP_DIR..."
rm -rf "$APP_DIR"
mkdir -p \
    "$APP_DIR/Contents/MacOS" \
    "$APP_DIR/Contents/Resources/app/backend/app" \
    "$APP_DIR/Contents/Resources/app/frontend/dist"

cp "$BUILT_BINARY" "$APP_DIR/Contents/MacOS/$EXECUTABLE_NAME"
chmod +x "$APP_DIR/Contents/MacOS/$EXECUTABLE_NAME"

cp "$ICON_PATH" "$APP_DIR/Contents/Resources/AppIcon.icns"

rsync -a \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.DS_Store' \
    "$ROOT_DIR/backend/app/" \
    "$APP_DIR/Contents/Resources/app/backend/app/"

if [[ -f "$ROOT_DIR/backend/__init__.py" ]]; then
    cp "$ROOT_DIR/backend/__init__.py" "$APP_DIR/Contents/Resources/app/backend/__init__.py"
else
    touch "$APP_DIR/Contents/Resources/app/backend/__init__.py"
fi

cp "$ROOT_DIR/backend/requirements.txt" "$APP_DIR/Contents/Resources/app/backend/requirements.txt"

rsync -a \
    --exclude='.DS_Store' \
    "$ROOT_DIR/frontend/dist/" \
    "$APP_DIR/Contents/Resources/app/frontend/dist/"

# Detect and record a verified Python 3 interpreter for BackendDaemonManager
PYTHON_CANDIDATES=()
if [[ -n "${COLAB_PYTHON_BIN:-}" ]]; then
    PYTHON_CANDIDATES+=("$COLAB_PYTHON_BIN")
fi
if ACTIVE_PY="$(command -v python3 2>/dev/null || true)" && [[ -n "$ACTIVE_PY" ]]; then
    PYTHON_CANDIDATES+=("$ACTIVE_PY")
fi
PYTHON_CANDIDATES+=(
    "/opt/homebrew/Caskroom/miniconda/base/bin/python3"
    "/opt/homebrew/Caskroom/miniforge/base/bin/python3"
    "/opt/homebrew/anaconda3/bin/python3"
    "/opt/homebrew/bin/python3"
    "/usr/local/bin/python3"
    "/usr/bin/python3"
)

VERIFIED_PYTHON=""
for py in "${PYTHON_CANDIDATES[@]}"; do
    if [[ -x "$py" ]] && "$py" -c "import fastapi, uvicorn, asyncssh, cryptography" >/dev/null 2>&1; then
        VERIFIED_PYTHON="$py"
        break
    fi
done

if [[ -n "$VERIFIED_PYTHON" ]]; then
    printf '%s\n' "$VERIFIED_PYTHON" > "$APP_DIR/Contents/Resources/app/.python_hint"
    echo "    Recorded verified Python interpreter in .python_hint: $VERIFIED_PYTHON"
else
    echo "    Warning: no Python 3 with (fastapi, uvicorn, asyncssh, cryptography) found at build time; skipping .python_hint" >&2
fi

printf 'APPL????' > "$APP_DIR/Contents/PkgInfo"

cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleDisplayName</key>
    <string>Colab Manager</string>
    <key>CFBundleExecutable</key>
    <string>ColabManager</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleIdentifier</key>
    <string>com.colabmanager.desktop</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>Colab Manager</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.2.0</string>
    <key>CFBundleVersion</key>
    <string>1.2.0</string>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.developer-tools</string>
    <key>LSArchitecturePriority</key>
    <array>
        <string>arm64</string>
    </array>
    <key>LSMinimumSystemVersion</key>
    <string>14.0</string>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsLocalNetworking</key>
        <true/>
    </dict>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSSupportsAutomaticGraphicsSwitching</key>
    <true/>
</dict>
</plist>
PLIST

# 5. Validate Info.plist
echo "==> [5/6] Validating Info.plist..."
plutil -lint "$APP_DIR/Contents/Info.plist"

# 6. Ad-hoc sign and strictly verify bundle
echo "==> [6/6] Code-signing and verifying Apple Silicon bundle..."
xattr -cr "$APP_DIR" 2>/dev/null || true
codesign --force --deep --sign - "$APP_DIR"
codesign --verify --deep --strict "$APP_DIR"

echo "==> Successfully built: $APP_DIR"
