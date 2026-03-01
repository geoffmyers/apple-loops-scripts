#!/bin/bash
#
# bundle_python.sh - Creates a self-contained Python environment for the app
#
# This script:
# 1. Creates a minimal Python installation in the app bundle
# 2. Installs required pip packages (librosa, soundfile, numpy)
# 3. Copies the convert_to_apple_loops.py script
#

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$(dirname "$PROJECT_DIR")")"
PYTHON_SCRIPT_SOURCE="$REPO_ROOT/python-scripts/apple-loops-scripts/convert_to_apple_loops.py"

# App bundle paths (set by Xcode or manually)
APP_BUNDLE="${BUILT_PRODUCTS_DIR:-$PROJECT_DIR/build}/${PRODUCT_NAME:-AppleLoopsConverter}.app"
RESOURCES_DIR="$APP_BUNDLE/Contents/Resources"
PYTHON_DIR="$RESOURCES_DIR/python"

echo "=== Apple Loops Converter - Python Bundler ==="
echo "Project Dir: $PROJECT_DIR"
echo "Repo Root: $REPO_ROOT"
echo "App Bundle: $APP_BUNDLE"
echo ""

# Find system Python
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

# Create directories
echo "Creating bundle directories..."
mkdir -p "$PYTHON_DIR"
mkdir -p "$RESOURCES_DIR/Scripts"

# Create virtual environment
echo "Creating Python virtual environment..."
$SYSTEM_PYTHON -m venv "$PYTHON_DIR/venv"

# Activate and install packages
echo "Installing required packages..."
source "$PYTHON_DIR/venv/bin/activate"

# Upgrade pip first
pip install --upgrade pip --quiet

# Install required packages
pip install librosa soundfile numpy --quiet

echo "Installed packages:"
pip list --format=freeze | grep -E "^(librosa|soundfile|numpy|scipy|scikit)"

deactivate

# Copy the conversion script
echo "Copying conversion script..."
if [ -f "$PYTHON_SCRIPT_SOURCE" ]; then
    cp "$PYTHON_SCRIPT_SOURCE" "$RESOURCES_DIR/Scripts/"
    echo "Copied: convert_to_apple_loops.py"
else
    echo "WARNING: Source script not found at $PYTHON_SCRIPT_SOURCE"
fi

# Create a wrapper script that uses the bundled Python
cat > "$RESOURCES_DIR/Scripts/run_converter.sh" << 'WRAPPER_EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESOURCES_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_VENV="$RESOURCES_DIR/python/venv"

# Activate virtual environment and run
source "$PYTHON_VENV/bin/activate"
python "$SCRIPT_DIR/convert_to_apple_loops.py" "$@"
WRAPPER_EOF
chmod +x "$RESOURCES_DIR/Scripts/run_converter.sh"

# Calculate bundle size
BUNDLE_SIZE=$(du -sh "$PYTHON_DIR" | cut -f1)
echo ""
echo "=== Bundle Complete ==="
echo "Python bundle size: $BUNDLE_SIZE"
echo "Location: $PYTHON_DIR"
echo ""

# Verify installation
echo "Verifying installation..."
"$PYTHON_DIR/venv/bin/python" -c "import librosa; import soundfile; import numpy; print('All packages imported successfully')"

echo "Done!"
