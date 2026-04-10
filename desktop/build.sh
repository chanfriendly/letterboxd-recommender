#!/usr/bin/env bash
# build.sh — build Letterboxd Recommender.app + .dmg for macOS
#
# Prerequisites (install once):
#   brew install redis           # source for redis-server binary
#   brew install create-dmg      # for DMG packaging (optional — skipped if absent)
#
# Usage:
#   cd desktop/
#   bash build.sh
#
# Output:
#   dist/Letterboxd Recommender.app
#   dist/Letterboxd Recommender.dmg  (if create-dmg is installed)

set -euo pipefail

APP_NAME="Letterboxd Recommender"
APP_BUNDLE="dist/${APP_NAME}.app"
CONTENTS="${APP_BUNDLE}/Contents"
MACOS="${CONTENTS}/MacOS"
RESOURCES="${CONTENTS}/Resources"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESKTOP_DIR="${REPO_ROOT}/desktop"

# ── Detect architecture ────────────────────────────────────────────────────────
ARCH=$(uname -m)
if [[ "$ARCH" == "arm64" ]]; then
    PBS_ARCH="aarch64-apple-darwin"
else
    PBS_ARCH="x86_64-apple-darwin"
fi

# python-build-standalone release — update version as needed
PBS_VERSION="20250317"
PBS_PYTHON="3.12.9"
PBS_FILENAME="cpython-${PBS_PYTHON}+${PBS_VERSION}-${PBS_ARCH}-install_only_stripped.tar.gz"
PBS_URL="https://github.com/indygreg/python-build-standalone/releases/download/${PBS_VERSION}/${PBS_FILENAME}"

echo "▶ Building for ${ARCH} (${PBS_ARCH})"
echo "▶ Repo root: ${REPO_ROOT}"

# ── Clean previous build ───────────────────────────────────────────────────────
rm -rf dist/
mkdir -p dist/

# ── Create .app directory structure ───────────────────────────────────────────
mkdir -p "${MACOS}"
mkdir -p "${RESOURCES}"

# ── Info.plist ─────────────────────────────────────────────────────────────────
cat > "${CONTENTS}/Info.plist" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Letterboxd Recommender</string>
    <key>CFBundleDisplayName</key>
    <string>Letterboxd Recommender</string>
    <key>CFBundleIdentifier</key>
    <string>com.chanfriendly.letterboxd-recommender</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>run</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
EOF

# ── Shell launcher ─────────────────────────────────────────────────────────────
cat > "${MACOS}/run" << 'EOF'
#!/usr/bin/env bash
# Launcher — sets paths and starts the Python menu bar app.
DIR="$(cd "$(dirname "$0")" && pwd)"
RESOURCES="${DIR}/../Resources"

export RESOURCEPATH="${RESOURCES}"
export PATH="${RESOURCES}/python/bin:${PATH}"

exec "${RESOURCES}/python/bin/python3" "${RESOURCES}/menubar.py"
EOF
chmod +x "${MACOS}/run"

# ── Download python-build-standalone ──────────────────────────────────────────
PBS_CACHE="${DESKTOP_DIR}/.cache/${PBS_FILENAME}"
mkdir -p "${DESKTOP_DIR}/.cache"

if [[ ! -f "${PBS_CACHE}" ]]; then
    echo "▶ Downloading Python ${PBS_PYTHON} (${PBS_ARCH})…"
    curl -fL --progress-bar -o "${PBS_CACHE}" "${PBS_URL}"
else
    echo "▶ Using cached Python ${PBS_PYTHON}"
fi

echo "▶ Extracting Python into bundle…"
tar -xzf "${PBS_CACHE}" -C "${RESOURCES}"
# python-build-standalone extracts as "python/" — rename if needed
if [[ -d "${RESOURCES}/python/install" ]]; then
    # Some releases nest under install/
    mv "${RESOURCES}/python/install" "${RESOURCES}/python_tmp"
    rm -rf "${RESOURCES}/python"
    mv "${RESOURCES}/python_tmp" "${RESOURCES}/python"
fi

BUNDLED_PY="${RESOURCES}/python/bin/python3"

# ── Install app dependencies ───────────────────────────────────────────────────
echo "▶ Installing Python dependencies…"
"${BUNDLED_PY}" -m pip install --quiet --no-warn-script-location \
    -r "${DESKTOP_DIR}/requirements.txt"

# ── Copy menubar.py into Resources ────────────────────────────────────────────
echo "▶ Copying app files…"
cp "${DESKTOP_DIR}/menubar.py" "${RESOURCES}/menubar.py"

# ── Copy FastAPI app source into Resources/src/app/ ───────────────────────────
# uvicorn imports "app.main:app" — the parent of the "app" package must be on PYTHONPATH.
# We set PYTHONPATH=Resources/src, so the package lives at Resources/src/app/.
mkdir -p "${RESOURCES}/src"
cp -r "${REPO_ROOT}/app" "${RESOURCES}/src/app"
# Remove __pycache__ and .pyc files
find "${RESOURCES}/src" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
echo "▶ App source copied to Resources/src/app/"

# ── Bundle redis-server ────────────────────────────────────────────────────────
echo "▶ Bundling redis-server…"
REDIS_BIN=$(which redis-server 2>/dev/null || echo "")
if [[ -z "${REDIS_BIN}" ]]; then
    echo "  ✗ redis-server not found — install with: brew install redis"
    echo "    Skipping Redis bundle. The app will use a system Redis if available."
else
    mkdir -p "${RESOURCES}/bin"
    cp "${REDIS_BIN}" "${RESOURCES}/bin/redis-server"
    echo "  ✓ redis-server bundled from ${REDIS_BIN}"
fi

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
echo "✓ Built: ${APP_BUNDLE}"
echo ""

# ── Create DMG (optional) ──────────────────────────────────────────────────────
if command -v create-dmg &>/dev/null; then
    echo "▶ Creating DMG…"
    create-dmg \
        --volname "${APP_NAME}" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --app-drop-link 450 185 \
        "dist/${APP_NAME}.dmg" \
        "${APP_BUNDLE}"
    echo "✓ DMG: dist/${APP_NAME}.dmg"
else
    echo "  (create-dmg not found — skipping DMG. Install with: brew install create-dmg)"
fi
