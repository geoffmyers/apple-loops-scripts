#!/usr/bin/env python3
"""Measure how much of a Splice library's tag vocabulary we can map.

Splice tags are an open vocabulary of a few hundred words; Apple's categories,
subcategories and genres are closed lists of 16, 82 and 30. Every Splice tag we
cannot place is a sample that lands in "Other Instrument / Other Genre" and
sits unfindable in the Loop Browser.

This reports coverage weighted by how often each tag actually occurs, so effort
goes where the samples are, and lists the unmapped tags in frequency order so
the mapping can be extended from data rather than from imagination.

Some tags are correctly unmapped: artist names (kshmr, medasin), decade tags
(80s, 90s) and production adjectives that are not instruments carry no
category. Those are reported separately from genuine gaps.

Usage:
    ./splice-tag-coverage.py --splice-db ~/path/to/sounds.db
    ./splice-tag-coverage.py --splice-db sounds.db --show-unmapped 60
"""

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from convert_to_apple_loops import (  # noqa: E402
    SPLICE_GENRE_TO_APPLE,
    SPLICE_TAG_TO_DESCRIPTOR,
    SPLICE_TAG_TO_INSTRUMENT,
    splice_genre_to_apple,
    splice_tags_to_instrument,
)

# Tags that legitimately map to nothing: they say who made the sample or when
# it sounds like it was made, not what instrument it is.
NON_INSTRUMENT_TAG_KINDS = {
    "artist or pack name": {
        "kshmr", "medasin", "krane", "stelouse", "multiplier", "mixmash",
    },
    "era": {
        "70s", "80s", "90s", "2000s", "old", "retro", "vintage",
    },
    "structure or workflow": {
        "songstarters", "songwriting", "arrangement", "bridge", "chorus",
        "buildup", "variety", "mix", "resampled", "chops", "layered",
        "melodic stack", "solo", "harmony", "music", "melody", "phrases",
    },
}
NON_INSTRUMENT_TAGS = set().union(*NON_INSTRUMENT_TAG_KINDS.values())


def read_tags(db_path: Path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    tags, genres, rows = Counter(), Counter(), 0
    for row in conn.execute(
            "SELECT tags, genre FROM samples WHERE local_path IS NOT NULL"):
        rows += 1
        for tag in (row["tags"] or "").split(","):
            tag = tag.strip().lower()
            if tag:
                tags[tag] += 1
        if row["genre"]:
            genres[row["genre"].strip().lower()] += 1
    conn.close()
    return tags, genres, rows


def coverage(tags: Counter, genres: Counter):
    mapped_tags = {t: n for t, n in tags.items() if t in SPLICE_TAG_TO_INSTRUMENT}
    descriptor_only = {t: n for t, n in tags.items()
                       if t not in SPLICE_TAG_TO_INSTRUMENT
                       and t in SPLICE_TAG_TO_DESCRIPTOR}
    genre_only = {t: n for t, n in tags.items()
                  if t not in SPLICE_TAG_TO_INSTRUMENT
                  and t not in SPLICE_TAG_TO_DESCRIPTOR
                  and t in SPLICE_GENRE_TO_APPLE}
    excused = {t: n for t, n in tags.items()
               if t in NON_INSTRUMENT_TAGS
               and t not in SPLICE_TAG_TO_INSTRUMENT}
    unmapped = {t: n for t, n in tags.items()
                if t not in SPLICE_TAG_TO_INSTRUMENT
                and t not in SPLICE_TAG_TO_DESCRIPTOR
                and t not in SPLICE_GENRE_TO_APPLE
                and t not in NON_INSTRUMENT_TAGS}

    total_occurrences = sum(tags.values())
    unmapped_genres = {g: n for g, n in genres.items()
                       if g not in SPLICE_GENRE_TO_APPLE}

    return {
        "distinct_tags": len(tags),
        "tag_occurrences": total_occurrences,
        "instrument_mapped_distinct": len(mapped_tags),
        "instrument_mapped_occurrences": sum(mapped_tags.values()),
        "descriptor_only": descriptor_only,
        "genre_only": genre_only,
        "excused": excused,
        "unmapped": dict(sorted(unmapped.items(), key=lambda kv: -kv[1])),
        "unmapped_occurrences": sum(unmapped.values()),
        "coverage_pct": round(
            100.0 * (total_occurrences - sum(unmapped.values())) / total_occurrences, 2
        ) if total_occurrences else None,
        "distinct_genres": len(genres),
        "unmapped_genres": dict(sorted(unmapped_genres.items(), key=lambda kv: -kv[1])),
        "genre_coverage_pct": round(
            100.0 * (sum(genres.values()) - sum(unmapped_genres.values()))
            / sum(genres.values()), 2) if genres else None,
    }


def sample_level_coverage(db_path: Path):
    """How many actual samples land somewhere better than the fallback."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    placed = fallback = 0
    genre_placed = genre_fallback = 0
    for row in conn.execute(
            "SELECT tags, genre FROM samples WHERE local_path IS NOT NULL"):
        tag_list = [t.strip().lower() for t in (row["tags"] or "").split(",") if t.strip()]
        category, _ = splice_tags_to_instrument(tag_list)
        if category == "Other Instrument":
            fallback += 1
        else:
            placed += 1
        if splice_genre_to_apple(row["genre"] or "", tag_list) == "Other Genre":
            genre_fallback += 1
        else:
            genre_placed += 1
    conn.close()
    total = placed + fallback
    return {
        "samples": total,
        "category_placed": placed,
        "category_fallback": fallback,
        "category_placed_pct": round(100.0 * placed / total, 2) if total else None,
        "genre_placed": genre_placed,
        "genre_fallback": genre_fallback,
        "genre_placed_pct": round(100.0 * genre_placed / total, 2) if total else None,
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--splice-db", type=Path, required=True)
    parser.add_argument("--show-unmapped", type=int, default=40,
                        help="How many unmapped tags to list")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    if not args.splice_db.exists():
        print(f"Error: no database at {args.splice_db}", file=sys.stderr)
        sys.exit(1)

    tags, genres, rows = read_tags(args.splice_db)
    cov = coverage(tags, genres)
    samples = sample_level_coverage(args.splice_db)

    print(f"Splice library: {args.splice_db}  ({rows:,} downloaded samples)\n")

    print("=" * 74)
    print("TAG COVERAGE (weighted by how often each tag occurs)")
    print("=" * 74)
    print(f"Distinct tags:                  {cov['distinct_tags']}")
    print(f"Tag occurrences:                {cov['tag_occurrences']:,}")
    print(f"Mapped to an instrument:        {cov['instrument_mapped_distinct']} tags, "
          f"{cov['instrument_mapped_occurrences']:,} occurrences")
    print(f"Mapped to a descriptor only:    {len(cov['descriptor_only'])} tags")
    print(f"Mapped to a genre only:         {len(cov['genre_only'])} tags")
    print(f"Correctly carry no instrument:  {len(cov['excused'])} tags "
          f"(artist names, eras, structure)")
    print(f"UNMAPPED:                       {len(cov['unmapped'])} tags, "
          f"{cov['unmapped_occurrences']:,} occurrences")
    print(f"Coverage:                       {cov['coverage_pct']}%")

    if cov["unmapped"]:
        print(f"\nTop unmapped tags:")
        for tag, n in list(cov["unmapped"].items())[:args.show_unmapped]:
            print(f"  {n:>5}  {tag}")

    print("\n" + "=" * 74)
    print("GENRE COVERAGE")
    print("=" * 74)
    print(f"Distinct genres:                {cov['distinct_genres']}")
    print(f"Coverage:                       {cov['genre_coverage_pct']}%")
    if cov["unmapped_genres"]:
        print("Unmapped genres:")
        for g, n in cov["unmapped_genres"].items():
            print(f"  {n:>5}  {g}")

    print("\n" + "=" * 74)
    print("WHERE THE SAMPLES ACTUALLY LAND")
    print("=" * 74)
    print(f"Samples:                        {samples['samples']:,}")
    print(f"Given a real category:          {samples['category_placed']:,} "
          f"({samples['category_placed_pct']}%)")
    print(f"Fell back to Other Instrument:  {samples['category_fallback']:,}")
    print(f"Given a real genre:             {samples['genre_placed']:,} "
          f"({samples['genre_placed_pct']}%)")
    print(f"Fell back to Other Genre:       {samples['genre_fallback']:,}")

    if args.json:
        args.json.write_text(json.dumps({"coverage": cov, "samples": samples}, indent=2))
        print(f"\nFull report: {args.json}")


if __name__ == "__main__":
    main()
