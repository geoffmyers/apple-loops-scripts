#!/bin/bash
#
# build_distributable.sh - Build a self-contained distributable app
#
# This script:
# 1. Builds the Xcode project in Release mode
# 2. Bundles Python virtual environment with required packages
# 3. Copies the conversion script
# 4. Creates a DMG for distribution (optional)
#

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$(dirname "$PROJECT_DIR")")"
BUILD_DIR="$PROJECT_DIR/build"
PYTHON_SCRIPT_SOURCE="$REPO_ROOT/python-scripts/apple-loops-scripts/convert_to_apple_loops.py"

# App names
APP_NAME="AppleLoopsConverter"
SCHEME_NAME="AppleLoopsConverter"

echo "=============================================="
echo "  Apple Loops Converter - Distributable Build"
echo "=============================================="
echo ""
echo "Project: $PROJECT_DIR"
echo "Repo Root: $REPO_ROOT"
echo "Build Dir: $BUILD_DIR"
echo ""

# Clean and create build directory
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# Step 1: Build the Xcode project
echo "Step 1: Building Xcode project..."
cd "$PROJECT_DIR"

xcodebuild \
    -project "$APP_NAME.xcodeproj" \
    -scheme "$SCHEME_NAME" \
    -configuration Release \
    -derivedDataPath "$BUILD_DIR/DerivedData" \
    clean build \
    CONFIGURATION_BUILD_DIR="$BUILD_DIR" \
    CODE_SIGN_IDENTITY="-" \
    CODE_SIGNING_REQUIRED=NO \
    | grep -E "^(Build|Compil|Link|Sign|error:|warning:)" || true

if [ ! -d "$BUILD_DIR/$APP_NAME.app" ]; then
    echo "ERROR: Build failed - app bundle not found"
    exit 1
fi

echo "Build completed: $BUILD_DIR/$APP_NAME.app"
echo ""

# Step 2: Find Python
echo "Step 2: Locating Python..."
SYSTEM_PYTHON=""
for py in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if [ -x "$py" ]; then
        SYSTEM_PYTHON="$py"
        break
    fi
done

if [ -z "$SYSTEM_PYTHON" ]; then
    echo "ERROR: Python 3 not found"
    exit 1
fi

PYTHON_VERSION=$($SYSTEM_PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Using Python: $SYSTEM_PYTHON (version $PYTHON_VERSION)"
echo ""

# Step 3: Create Python virtual environment in app bundle
echo "Step 3: Creating Python environment in app bundle..."
RESOURCES_DIR="$BUILD_DIR/$APP_NAME.app/Contents/Resources"
PYTHON_DIR="$RESOURCES_DIR/python"
SCRIPTS_DIR="$RESOURCES_DIR/Scripts"

mkdir -p "$PYTHON_DIR"
mkdir -p "$SCRIPTS_DIR"

echo "Creating virtual environment..."
$SYSTEM_PYTHON -m venv "$PYTHON_DIR/venv"

echo "Installing required packages..."
source "$PYTHON_DIR/venv/bin/activate"
pip install --upgrade pip --quiet
pip install librosa soundfile numpy --quiet

echo "Installed packages:"
pip list --format=freeze | grep -E "^(librosa|soundfile|numpy|scipy|scikit)" || echo "  (filtering packages)"

deactivate
echo ""

# Step 4: Copy the conversion script
echo "Step 4: Copying conversion script..."
if [ -f "$PYTHON_SCRIPT_SOURCE" ]; then
    cp "$PYTHON_SCRIPT_SOURCE" "$SCRIPTS_DIR/"
    echo "Copied: convert_to_apple_loops.py"
else
    echo "ERROR: Source script not found at $PYTHON_SCRIPT_SOURCE"
    exit 1
fi
echo ""

# Step 5: Verify the installation
echo "Step 5: Verifying installation..."
"$PYTHON_DIR/venv/bin/python" -c "
import sys
print(f'Python: {sys.version}')
import librosa
print(f'librosa: {librosa.__version__}')
import soundfile
print(f'soundfile: {soundfile.__version__}')
import numpy
print(f'numpy: {numpy.__version__}')
print('All packages verified successfully!')
"
echo ""

# Step 6: Calculate sizes
echo "Step 6: Build summary..."
APP_SIZE=$(du -sh "$BUILD_DIR/$APP_NAME.app" | cut -f1)
PYTHON_SIZE=$(du -sh "$PYTHON_DIR" | cut -f1)

echo "  App bundle size: $APP_SIZE"
echo "  Python environment: $PYTHON_SIZE"
echo "  Location: $BUILD_DIR/$APP_NAME.app"
echo ""

# Step 7: Create DMG (optional)
if command -v hdiutil &> /dev/null; then
    echo "Step 7: Creating DMG..."
    DMG_NAME="$APP_NAME-$(date +%Y%m%d).dmg"
    DMG_PATH="$BUILD_DIR/$DMG_NAME"

    # Create temporary directory for DMG contents
    DMG_TEMP="$BUILD_DIR/dmg_temp"
    mkdir -p "$DMG_TEMP"
    cp -R "$BUILD_DIR/$APP_NAME.app" "$DMG_TEMP/"

    # Create symlink to Applications
    ln -s /Applications "$DMG_TEMP/Applications"

    # Create DMG
    hdiutil create -volname "$APP_NAME" \
        -srcfolder "$DMG_TEMP" \
        -ov -format UDZO \
        "$DMG_PATH" \
        > /dev/null 2>&1 || true

    # Cleanup
    rm -rf "$DMG_TEMP"

    if [ -f "$DMG_PATH" ]; then
        DMG_SIZE=$(du -sh "$DMG_PATH" | cut -f1)
        echo "  DMG created: $DMG_PATH ($DMG_SIZE)"
    else
        echo "  DMG creation skipped (hdiutil error)"
    fi
    echo ""
fi

echo "=============================================="
echo "  Build Complete!"
echo "=============================================="
echo ""
echo "The self-contained app is ready at:"
echo "  $BUILD_DIR/$APP_NAME.app"
echo ""
echo "To run: open \"$BUILD_DIR/$APP_NAME.app\""
echo ""
