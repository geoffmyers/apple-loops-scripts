"""What the converter actually needs installed.

librosa (and the numpy stack it pulls in) is only needed for transient
detection. Requiring it to convert a MIDI file, or to run with
--no-transient-detection, makes the tool uninstallable on a stock macOS Python
for no reason -- and that is where it runs.
"""

import builtins
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_converter_imports_without_numpy():
    """Import it in a subprocess with numpy blocked at the import hook."""
    script = (
        "import sys\n"
        "class Block:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name.split('.')[0] in ('numpy', 'librosa', 'soundfile'):\n"
        "            raise ImportError(f'{name} blocked for this test')\n"
        "        return None\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        return self.find_module(name, path)\n"
        "sys.meta_path.insert(0, Block())\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import convert_to_apple_loops as c\n"
        "print(c.snap_beat_count(16.75, 4))\n"
    )
    result = subprocess.run([sys.executable, "-c", script],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "16"
