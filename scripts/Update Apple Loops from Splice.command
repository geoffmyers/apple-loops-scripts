#!/bin/bash
#
# Double-click this to import every Splice sample downloaded since last time
# into Logic Pro's Apple Loops library.
#
# Everything is self-contained: on first run it builds a small Python
# environment next to itself, which takes a minute or two. After that a sync
# takes seconds unless there is a lot of new material.
#
# It never touches your Splice folder, and never re-converts work it has
# already done.

set -uo pipefail

# Resolve symlinks so an alias on the Desktop or in the Dock still finds the
# toolkit it belongs to.
SELF="${BASH_SOURCE[0]}"
while [ -L "$SELF" ]; do
    LINK="$(readlink "$SELF")"
    case "$LINK" in
        /*) SELF="$LINK" ;;
        *)  SELF="$(dirname "$SELF")/$LINK" ;;
    esac
done
HERE="$(cd "$(dirname "$SELF")" && pwd)"

# Works whether this launcher sits in the toolkit root or in its scripts/
# folder, so it can be put wherever it is easiest to reach.
if [ -f "$HERE/sync_splice_to_apple_loops.py" ]; then
    SYNC="$HERE/sync_splice_to_apple_loops.py"
    TOOLKIT="$(cd "$HERE/.." && pwd)"
elif [ -f "$HERE/scripts/sync_splice_to_apple_loops.py" ]; then
    SYNC="$HERE/scripts/sync_splice_to_apple_loops.py"
    TOOLKIT="$HERE"
else
    SYNC=""
    TOOLKIT="$HERE"
fi
VENV="$TOOLKIT/.venv"

printf '\033]0;Update Apple Loops from Splice\007'   # window title
clear

echo "════════════════════════════════════════════════════════════"
echo "  Update Apple Loops from Splice"
echo "════════════════════════════════════════════════════════════"
echo

finish() {
    echo
    echo "────────────────────────────────────────────────────────────"
    echo "  Done. Close this window, or press any key."
    # -n 1 keeps the window up so the summary is readable; Terminal
    # otherwise closes or leaves a bare "Process completed".
    read -r -n 1 -s
    exit "${1:-0}"
}

if [ -z "$SYNC" ]; then
    echo "  Cannot find sync_splice_to_apple_loops.py."
    echo "  Keep this launcher in the Apple Loops Toolkit folder, or in its"
    echo "  scripts subfolder."
    finish 1
fi

# --- Python -----------------------------------------------------------------
# Prefer a Homebrew Python: the system one cannot always build librosa's wheels.
PYTHON=""
for candidate in \
    /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
    /opt/homebrew/bin/python3 /usr/local/bin/python3 \
    "$(command -v python3 || true)" /usr/bin/python3
do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then PYTHON="$candidate"; break; fi
done

if [ -z "$PYTHON" ]; then
    echo "  No Python 3 found. Install it from python.org or with Homebrew,"
    echo "  then double-click this again."
    finish 1
fi

# --- Environment ------------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
    echo "  First run: setting up (a minute or two, only happens once)..."
    echo
    "$PYTHON" -m venv "$VENV" || { echo "  Could not create the environment."; finish 1; }
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    if ! "$VENV/bin/python" -m pip install --quiet librosa soundfile numpy; then
        echo
        echo "  Could not install librosa. The sync will still run, but beat"
        echo "  markers will be placed on an even grid rather than on the"
        echo "  actual transients, which stretches less cleanly."
        echo
    fi
    echo "  Setup complete."
    echo
fi

# --- Sync -------------------------------------------------------------------
"$VENV/bin/python" "$SYNC" "$@"
STATUS=$?

if [ $STATUS -ne 0 ]; then
    echo
    echo "  Something went wrong (exit $STATUS). The output above says what."
fi

finish $STATUS
