#!/usr/bin/env python3
"""Export the name and Apple's own labels of every shipped Apple Loop.

Apple's loops are the only labelled data this project has: each carries a
human-written name ("Disco Delight Slap Bass") and the category, subcategory,
genre and descriptors Apple assigned it. That makes them ground truth for
anything that guesses those labels from a name or a tag list -- the filename
extractor in `convert_to_apple_loops.py`, or a model.

The library is only installed on macOS, so run this there and copy the JSON
wherever the comparison runs. Loops sharing a name stem ("... 01", "... 02")
are kept: the scorer decides whether to deduplicate.

Usage:
    ./export-apple-loop-labels.py --out apple-loop-labels.json
    ./export-apple-loop-labels.py --library "/Library/Audio/Apple Loops" --workers 8 --out labels.json
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

# The validator's filename has dashes, so it cannot be imported by name. It is
# registered in sys.modules because its worker pool pickles read_reference by
# module name, and macOS spawns workers that must find it again.
_spec = importlib.util.spec_from_file_location(
    "validate_against_apple_loops", HERE / "validate-against-apple-loops.py")
_validate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _validate
_spec.loader.exec_module(_validate)

FIELDS = ("category", "subcategory", "genre", "descriptors")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", type=Path, default=_validate.DEFAULT_LIBRARY)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None,
                        help="Deterministic sample instead of the whole library")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    loops = _validate.find_loops(args.library, limit=args.limit)
    if not loops:
        sys.exit(f"No .caf files under {args.library}")

    records, errors = [], 0
    for ref in _validate.collect(loops, args.workers):
        if "error" in ref:
            errors += 1
            continue
        path = Path(ref["path"])
        records.append({
            "name": path.stem,
            "pack": path.parent.name,
            **{field: ref.get(field) or "" for field in FIELDS},
        })

    args.out.write_text(json.dumps({
        "library": str(args.library),
        "loops": len(records),
        "decode_errors": errors,
        "records": records,
    }, indent=1))
    print(f"{len(records):,} loops written to {args.out} ({errors} could not be decoded)")


if __name__ == "__main__":
    main()
