#!/usr/bin/env python3
"""Keep Logic's Apple Loops library in step with a Splice sample folder.

Converts anything Splice has downloaded since the last run into Apple Loops and
drops it where Logic's Loop Browser will find it. Safe to run as often as you
like: it converts only what is new or has changed.

Repeatability is the whole design problem. The converter suffixes a taken
output name (Kick_01_2.caf), which is correct when two packs both contain a
Kick_01 and catastrophic on a re-run -- every sync would duplicate the library.
So this keeps a manifest keyed by source path, remembers the output name it
allocated, and leaves finished work alone.

Usage:
    ./sync_splice_to_apple_loops.py                 # sync what is new
    ./sync_splice_to_apple_loops.py --dry-run       # say what would happen
    ./sync_splice_to_apple_loops.py --rebuild       # reconvert everything
"""

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from convert_to_apple_loops import (  # noqa: E402
    AUDIO_EXTENSIONS,
    MIDI_EXTENSIONS,
    AppleLoopConverter,
    OnsetDetectionConfig,
)

# Where Splice puts downloads, and where Logic looks for user loops. The
# Splice subfolder keeps this tool's output separable from loops added by hand.
DEFAULT_SOURCE = Path.home() / "Splice" / "sounds"
DEFAULT_OUTPUT = (Path.home() / "Library" / "Audio" / "Apple Loops"
                  / "User Loops" / "Splice")
DEFAULT_MANIFEST = (Path.home() / "Library" / "Application Support"
                    / "apple-loops-sync" / "splice-sync-manifest.json")

CONVERTIBLE_EXTENSIONS = AUDIO_EXTENSIONS + MIDI_EXTENSIONS

MANIFEST_VERSION = 1


# ---------------------------------------------------------------------------

@dataclass
class SyncItem:
    source: Path
    output: Path
    reason: str = "new"


@dataclass
class SyncPlan:
    new: List[SyncItem] = field(default_factory=list)
    changed: List[SyncItem] = field(default_factory=list)
    unchanged: List[SyncItem] = field(default_factory=list)
    #: Already present in the output folder from before this manifest existed.
    adopted: List[SyncItem] = field(default_factory=list)

    @property
    def to_convert(self) -> List[SyncItem]:
        return self.new + self.changed


class Manifest:
    """What this tool has already converted, keyed by absolute source path.

    Stores the source's size and mtime so a re-downloaded or edited sample is
    noticed, and the output name that was allocated so it stays stable.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.entries: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            self.entries = dict(data.get("entries") or {})
        except (json.JSONDecodeError, OSError, AttributeError):
            # A corrupt manifest costs a full reconversion, not a crash. The
            # alternative -- refusing to run -- leaves the user with nothing.
            self.entries = {}

    def knows(self, source: Path) -> bool:
        return str(Path(source).resolve()) in self.entries

    def entry(self, source: Path) -> Optional[dict]:
        return self.entries.get(str(Path(source).resolve()))

    def record(self, source: Path, output: Path) -> None:
        source, output = Path(source), Path(output)
        try:
            stat = source.stat()
            size, mtime = stat.st_size, stat.st_mtime
        except OSError:
            size, mtime = 0, 0.0
        self.entries[str(source.resolve())] = {
            "output": str(output),
            "size": size,
            "mtime": mtime,
        }

    def forget(self, source: Path) -> None:
        self.entries.pop(str(Path(source).resolve()), None)

    def allocated_names(self) -> set:
        return {Path(e["output"]).name for e in self.entries.values()}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": MANIFEST_VERSION,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "entries": self.entries,
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=1))
        os.replace(tmp, self.path)


# ---------------------------------------------------------------------------

def find_sources(source_dir: Path) -> List[Path]:
    """Every convertible file under the Splice folder.

    Directories whose names end in .wav exist in real Splice trees, so the
    is_file() check is not paranoia.
    """
    source_dir = Path(source_dir)
    if not source_dir.exists():
        return []
    found = set()
    for ext in CONVERTIBLE_EXTENSIONS:
        for pattern in (f"**/*{ext}", f"**/*{ext.upper()}"):
            found.update(p for p in source_dir.glob(pattern) if p.is_file())
    return sorted(found)


def plan(source_dir: Path, manifest: Manifest, output_dir: Path,
         rebuild: bool = False) -> SyncPlan:
    """Decide what needs converting, without converting anything."""
    output_dir = Path(output_dir)
    result = SyncPlan()

    # Keyed case-insensitively: macOS treats Foo.caf and foo.caf as one file,
    # so two sources whose stems differ only in case would otherwise be given
    # "distinct" names that overwrite each other. That cost one of 1,810 owned
    # samples on a real library before this was keyed this way.
    taken = {n.casefold() for n in manifest.allocated_names()}

    # The actual .caf already in the output folder, keyed case-insensitively.
    # Resolving through this rather than Path.exists() means the sync behaves
    # the same on a case-sensitive filesystem as on macOS, instead of
    # inheriting whatever the volume happens to do.
    on_disk: Dict[str, Path] = {}
    if output_dir.exists():
        for existing_file in output_dir.glob("*.caf"):
            on_disk.setdefault(existing_file.name.casefold(), existing_file)
    taken.update(on_disk)

    # Every output name handed out during this plan. Two sources sharing one
    # .caf means one sample's audio silently overwrites another's, so a name
    # can be claimed exactly once -- including by adoption, and including by
    # a manifest that recorded a double-booking before this check existed.
    claimed: Dict[str, Path] = {}

    def claim(name: str, source: Path) -> bool:
        key = name.casefold()
        if claimed.get(key, source) != source:
            return False
        claimed[key] = source
        return True

    for source in find_sources(source_dir):
        entry = manifest.entry(source)

        if entry and not rebuild:
            output = Path(entry["output"])
            try:
                stat = source.stat()
                same = (stat.st_size == entry.get("size")
                        and abs(stat.st_mtime - entry.get("mtime", 0)) < 1)
            except OSError:
                same = False

            if same and output.exists() and claim(output.name, source):
                result.unchanged.append(SyncItem(source, output, "unchanged"))
                continue

            if not claim(output.name, source):
                # Another source already owns this name. Give this one its own.
                output = _allocate(output_dir, source, taken | set(claimed))
                claim(output.name, source)
                taken.add(output.name.casefold())
                result.changed.append(SyncItem(source, output, "name conflict"))
                continue

            reason = "source changed" if not same else "output missing"
            result.changed.append(SyncItem(source, output, reason))
            continue

        if entry and rebuild:
            output = Path(entry["output"])
            if not claim(output.name, source):
                output = _allocate(output_dir, source, taken | set(claimed))
                claim(output.name, source)
                taken.add(output.name.casefold())
            result.changed.append(SyncItem(source, output, "rebuild"))
            continue

        # An output folder can already hold .caf files -- from an earlier
        # version of this toolkit, or a run before the manifest existed.
        # Allocating around them would suffix every one and double the
        # library, so a file sitting at exactly the name this source would
        # have been given is adopted instead. Only the first source to want
        # that name may have it.
        existing = on_disk.get(f"{source.stem}.caf".casefold())
        if not rebuild and existing is not None and claim(existing.name, source):
            result.adopted.append(SyncItem(source, existing, "already present"))
            continue

        output = _allocate(output_dir, source, taken | set(claimed))
        claim(output.name, source)
        taken.add(output.name.casefold())
        result.new.append(SyncItem(source, output, "new"))

    return result


def _allocate(output_dir: Path, source: Path, taken: set) -> Path:
    """Pick an unused .caf name for a source file.

    Sample packs repeat basenames constantly, so a suffix is needed -- but only
    for a genuinely different source. Names already allocated to another source
    are held in `taken`, case-folded, so one run cannot hand out the same name
    twice on a case-insensitive filesystem.
    """
    stem = source.stem
    candidate = f"{stem}.caf"
    index = 2
    while candidate.casefold() in taken:
        candidate = f"{stem}_{index}.caf"
        index += 1
    return output_dir / candidate


# ---------------------------------------------------------------------------

def run(source_dir: Path, output_dir: Path, manifest: Manifest,
        splice_db_path: Optional[Path], rebuild: bool, dry_run: bool,
        quiet: bool = False) -> dict:
    """Convert everything the plan says needs converting."""
    splice_library = None
    if splice_db_path:
        from splice_db import SpliceLibrary
        try:
            splice_library = SpliceLibrary(splice_db_path)
        except (FileNotFoundError, ValueError) as exc:
            print(f"  Splice database unusable ({exc}); falling back to "
                  f"filenames.", file=sys.stderr)

    converter = AppleLoopConverter(
        output_dir=output_dir,
        splice_library=splice_library,
        onset_config=OnsetDetectionConfig(),
    )

    work = plan(source_dir, manifest, output_dir, rebuild)
    stats = {
        "total": (len(work.new) + len(work.changed) + len(work.unchanged)
                  + len(work.adopted)),
        "new": len(work.new),
        "changed": len(work.changed),
        "unchanged": len(work.unchanged),
        "adopted": len(work.adopted),
        "converted": 0,
        "failed": 0,
        "from_splice": 0,
        "loops": 0,
        "one_shots": 0,
    }

    if dry_run:
        return stats

    # Adopting costs nothing and stops the next run rediscovering them.
    for item in work.adopted:
        manifest.record(item.source, item.output)
    if work.adopted:
        manifest.save()

    if not work.to_convert:
        return stats

    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(work.to_convert)

    for i, item in enumerate(work.to_convert, 1):
        metadata = converter.metadata_for(item.source, relative_to=source_dir)
        result = converter.convert_file(item.source, item.output, metadata=metadata)

        if result:
            manifest.record(item.source, item.output)
            stats["converted"] += 1
            stats["one_shots" if metadata.is_one_shot else "loops"] += 1
            if metadata.metadata_source == "splice":
                stats["from_splice"] += 1
        else:
            manifest.forget(item.source)
            stats["failed"] += 1

        if not quiet and (i % 25 == 0 or i == total):
            print(f"  {i}/{total} converted...", flush=True)

        # Save periodically so an interrupted run does not redo everything.
        if i % 100 == 0:
            manifest.save()

    manifest.save()
    return stats


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help=f"Splice sounds folder (default: {DEFAULT_SOURCE})")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"Apple Loops destination (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                        help="Where to remember what has been converted")
    parser.add_argument("--splice-db", type=Path,
                        help="Path to sounds.db (found automatically by default)")
    parser.add_argument("--rebuild", action="store_true",
                        help="Reconvert everything, not just what is new")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be converted and stop")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    print("Splice to Apple Loops")
    print("=" * 58)

    if not args.source.exists():
        print(f"\nNo Splice folder at {args.source}")
        print("Pass --source if your Splice downloads live somewhere else.")
        return 1

    db_path = args.splice_db
    if db_path is None:
        from splice_db import find_sounds_db
        db_path = find_sounds_db()

    print(f"  From:     {args.source}")
    print(f"  To:       {args.output}")
    print(f"  Metadata: {db_path if db_path else 'filenames only (no Splice database found)'}")
    print()

    manifest = Manifest(args.manifest)
    stats = run(args.source, args.output, manifest, db_path,
                args.rebuild, args.dry_run, args.quiet)

    print()
    print("=" * 58)
    if args.dry_run:
        print(f"  Would convert:  {stats['new']} new, {stats['changed']} changed")
        print(f"  Already done:   {stats['unchanged'] + stats['adopted']}")
        if stats["adopted"]:
            print(f"    ...of which {stats['adopted']} are already in the output "
                  f"folder from an earlier run.")
            print(f"    Run with --rebuild to reconvert them.")
    elif stats["converted"] == 0 and stats["failed"] == 0:
        already = stats["unchanged"] + stats["adopted"]
        print(f"  Nothing new. {already} loops already in your library.")
    else:
        print(f"  Converted:      {stats['converted']} "
              f"({stats['loops']} loops, {stats['one_shots']} one-shots)")
        if stats["from_splice"]:
            print(f"  Splice metadata: {stats['from_splice']}/{stats['converted']}")
        if stats["failed"]:
            print(f"  Failed:         {stats['failed']}")
        print(f"  Already done:   {stats['unchanged'] + stats['adopted']}")
        print()
        print("  Restart Logic Pro to index the new loops.")

    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
