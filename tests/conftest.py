"""Shared pytest configuration for apple-loops-scripts."""

import sys
from pathlib import Path

# The CLI scripts live at the repo root, not in a package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
