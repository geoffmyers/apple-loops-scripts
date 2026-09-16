#!/usr/bin/env python3
"""Validate this toolkit against Apple's own shipped Apple Loops.

Logic and GarageBand install tens of thousands of loops that Apple authored, so
the format's real value domains and real beat counts are sitting on disk. This
checks our assumptions against them instead of against our own documentation.

Three checks, each independently useful:

**vocabulary** -- collect every category, subcategory, genre, descriptor, key
type and time signature Apple actually ships, and diff against the lists in
`apple_loops_vocabulary.py`. Those lists were curated by hand; this is the only
way to know whether they are right.

**beatcount** -- Apple stores no tempo, so a loop's tempo is
`beat_count * 60 / duration`. Take Apple's own beat count as truth, derive the
tempo, round it the way a human or a sample library would report it, and check
`snap_beat_count()` recovers the beat count Apple wrote. This measures the
snapping algorithm against real music rather than invented cases.

**roundtrip** -- strip a loop to bare audio with `afconvert`, run it back
through the converter with the metadata a user would supply, decode the result
and diff it against the original. This is the end-to-end proof.

Usage:
    ./validate-against-apple-loops.py --mode all --limit 500
    ./validate-against-apple-loops.py --mode vocabulary          # whole library
    ./validate-against-apple-loops.py --mode roundtrip --limit 100 --json out.json
"""

import argparse
import json
import random
import subprocess
import sys
import tempfile
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apple_loops_vocabulary import (  # noqa: E402
    APPLE_CATEGORIES,
    APPLE_DESCRIPTORS,
    APPLE_GENRES,
    APPLE_SUBCATEGORIES,
)
from convert_to_apple_loops import (  # noqa: E402
    AppleLoopConverter,
    LoopMetadata,
    beats_per_bar,
    snap_beat_count,
)
from decode_apple_loops import AppleLoopDecoder, verify_apple_loop  # noqa: E402

DEFAULT_LIBRARY = Path("/Library/Audio/Apple Loops")


# ---------------------------------------------------------------------------
# Collecting reference data
# ---------------------------------------------------------------------------

def find_loops(library: Path, limit=None, seed: int = 1507):
    """Every .caf under the library, optionally a deterministic sample."""
    loops = sorted(library.rglob("*.caf"))
    if limit and limit < len(loops):
        random.Random(seed).shuffle(loops)
        loops = sorted(loops[:limit])
    return loops


def read_reference(path):
    """Decode one Apple Loop into the fields we compare on."""
    try:
        info = AppleDecoderPool.decoder().decode_file(Path(path))
    except Exception as exc:                                  # pragma: no cover
        return {"path": str(path), "error": str(exc)}

    meta = info.metadata
    duration = info.audio.duration or 0.0
    return {
        "path": str(path),
        "category": meta.category,
        "subcategory": meta.subcategory,
        "genre": meta.genre,
        "beat_count": meta.beat_count,
        "time_signature": meta.time_signature,
        "key_signature": meta.key_signature,
        "key_type": meta.key_type,
        "descriptors": meta.descriptors,
        "duration": duration,
        "num_frames": info.audio.num_frames,
        "sample_rate": info.audio.sample_rate,
        "codec": info.audio.codec,
        "marker_count": info.beat_markers.marker_count,
        "last_marker": (info.beat_markers.positions[-1]
                        if info.beat_markers.positions else None),
        "positions": list(info.beat_markers.positions),
        "spotlight_genre": info.spotlight.entries.get("genre", ""),
        "chunks": dict(info.raw_chunks),
    }


class AppleDecoderPool:
    """One decoder per worker process; constructing it per file is wasteful."""
    _decoder = None

    @classmethod
    def decoder(cls):
        if cls._decoder is None:
            cls._decoder = AppleLoopDecoder(verbose=False)
        return cls._decoder


def collect(loops, workers: int):
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return [r for r in pool.map(read_reference, [str(p) for p in loops],
                                    chunksize=32)]


# ---------------------------------------------------------------------------
# Check 1: vocabulary
# ---------------------------------------------------------------------------

def check_vocabulary(refs):
    """Diff Apple's shipped values against our curated lists."""
    fields = {
        "category": (APPLE_CATEGORIES, "category"),
        "subcategory": (APPLE_SUBCATEGORIES, "subcategory"),
        "genre": (APPLE_GENRES, "genre"),
    }

    report = {}
    for field, (known, key) in fields.items():
        seen = Counter(r[key] for r in refs if r.get(key))
        missing = {v: c for v, c in seen.items() if v not in known}
        unused = [v for v in known if v not in seen]
        report[field] = {
            "distinct_in_library": len(seen),
            "missing_from_our_list": dict(sorted(missing.items(),
                                                 key=lambda kv: -kv[1])),
            "in_our_list_but_never_shipped": sorted(unused),
            "most_common": dict(seen.most_common(15)),
        }

    descriptors = Counter()
    for r in refs:
        for d in (r.get("descriptors") or "").split(","):
            if d.strip():
                descriptors[d.strip()] += 1
    report["descriptors"] = {
        "distinct_in_library": len(descriptors),
        "missing_from_our_list": dict(sorted(
            ((d, c) for d, c in descriptors.items() if d not in APPLE_DESCRIPTORS),
            key=lambda kv: -kv[1])),
        "in_our_list_but_never_shipped": sorted(
            d for d in APPLE_DESCRIPTORS if d not in descriptors),
        "most_common": dict(descriptors.most_common(20)),
    }

    # The complete shipped vocabulary, and which subcategories Apple actually
    # pairs with each category. Emitting a combination Apple never ships is as
    # invisible in the Loop Browser as an unknown value.
    shipped = defaultdict(Counter)
    for r in refs:
        if r.get("category"):
            shipped[r["category"]][r.get("subcategory") or ""] += 1
    report["shipped"] = {
        "categories": dict(Counter(r["category"] for r in refs
                                   if r.get("category")).most_common()),
        "subcategories": dict(Counter(r["subcategory"] for r in refs
                                      if r.get("subcategory")).most_common()),
        "genres": dict(Counter(r["genre"] for r in refs if r.get("genre")).most_common()),
        "descriptors": dict(descriptors.most_common()),
        "category_subcategory_pairs": {
            cat: dict(subs.most_common()) for cat, subs in sorted(shipped.items())
        },
    }

    report["key_type"] = dict(Counter(r.get("key_type", "") for r in refs).most_common())
    report["time_signature"] = dict(
        Counter(r.get("time_signature", "") for r in refs).most_common())
    report["codec"] = dict(Counter(r.get("codec", "") for r in refs).most_common())
    return report


# ---------------------------------------------------------------------------
# Check 2: beat count recovery
# ---------------------------------------------------------------------------

def check_beat_counts(refs, tail_beats: float = 0.0, seed: int = 1507):
    """Can snap_beat_count() recover Apple's own beat count?

    Apple's beat count and duration give the exact tempo. A user (or a sample
    library's metadata) reports that tempo rounded to a whole number, which is
    what the converter is given. The question is whether the beat count comes
    back out.

    Apple's own loops are trimmed exactly, so on the raw library both snapping
    and plain rounding score 100% -- which proves snapping is harmless but
    proves nothing about its value. `tail_beats` simulates what a third-party
    sample actually looks like: the same music exported with up to that much
    decay past the final beat. That is the case snapping exists for.
    """
    total = matched = 0
    mismatches = []
    by_error = Counter()
    naive_matched = 0
    rng = random.Random(seed)

    for r in refs:
        beats = r.get("beat_count") or 0
        duration = r.get("duration") or 0.0
        if beats <= 0 or duration <= 0:
            continue

        total += 1
        bpb = beats_per_bar(r.get("time_signature") or "4/4")
        exact_tempo = beats * 60.0 / duration
        reported_tempo = int(round(exact_tempo))

        # The tempo the sample is really at does not change when a tail is
        # left on the export; only the file's duration does.
        measured_duration = duration
        if tail_beats:
            measured_duration += rng.uniform(0.0, tail_beats) * 60.0 / exact_tempo

        raw = reported_tempo * measured_duration / 60.0
        ours = snap_beat_count(raw, bpb)
        naive = int(round(raw))

        if ours == beats:
            matched += 1
        else:
            by_error[ours - beats] += 1
            if len(mismatches) < 40:
                mismatches.append({
                    "path": r["path"],
                    "apple_beats": beats,
                    "ours": ours,
                    "naive_rounding": naive,
                    "duration": round(duration, 4),
                    "measured_duration": round(measured_duration, 4),
                    "exact_tempo": round(exact_tempo, 3),
                    "reported_tempo": reported_tempo,
                    "time_signature": r.get("time_signature"),
                })
        if naive == beats:
            naive_matched += 1

    return {
        "tail_beats_simulated": tail_beats,
        "loops_tested": total,
        "recovered": matched,
        "recovered_pct": round(100.0 * matched / total, 3) if total else None,
        "naive_rounding_recovered": naive_matched,
        "naive_rounding_pct": round(100.0 * naive_matched / total, 3) if total else None,
        "error_distribution": dict(sorted(by_error.items())),
        "examples": mismatches,
    }


# ---------------------------------------------------------------------------
# Check 3: full round trip
# ---------------------------------------------------------------------------

COMPARED_FIELDS = ("category", "subcategory", "genre", "beat_count",
                   "time_signature", "key_signature", "key_type", "descriptors")


def strip_to_wav(src: Path, dest: Path) -> bool:
    """Decode an Apple Loop to a bare WAV, dropping every metadata chunk."""
    result = subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16", str(src), str(dest)],
        capture_output=True, text=True, timeout=120)
    return result.returncode == 0 and dest.exists()


def roundtrip_one(ref, workdir: Path, lossy: bool = False):
    """Strip a loop to audio, rebuild it, and diff against the original."""
    src = Path(ref["path"])
    out = {"path": ref["path"], "name": src.name}

    with tempfile.TemporaryDirectory(dir=str(workdir)) as tmp:
        tmp = Path(tmp)
        stripped = tmp / f"{src.stem}.wav"
        if not strip_to_wav(src, stripped):
            out["error"] = "afconvert failed to strip"
            return out

        # The metadata a user legitimately has: the loop's musical facts.
        # Deliberately NOT the beat count -- that is what we are testing.
        exact_tempo = (ref["beat_count"] * 60.0 / ref["duration"]
                       if ref["beat_count"] and ref["duration"] else None)
        # Apple's values verbatim, empties included. Substituting a default
        # here would be the harness inventing data and then reporting the
        # difference as a converter fault -- which is exactly what the first
        # run of this script did.
        metadata = LoopMetadata(
            category=ref["category"],
            subcategory=ref["subcategory"],
            genre=ref["genre"],
            time_signature=ref["time_signature"],
            key_signature=ref["key_signature"],
            key_type=ref["key_type"],
            descriptors=ref["descriptors"],
            tempo=int(round(exact_tempo)) if exact_tempo else None,
            is_one_shot=not ref["beat_count"],
        )

        converter = AppleLoopConverter(output_dir=tmp, lossy=lossy)
        produced = converter.convert_file(stripped, tmp / f"{src.stem}.caf",
                                          metadata=metadata)
        if not produced:
            out["error"] = "conversion failed"
            return out

        try:
            info = AppleDecoderPool.decoder().decode_file(produced)
        except Exception as exc:
            out["error"] = f"decode failed: {exc}"
            return out

        got = info.metadata
        diffs = {}
        for field in COMPARED_FIELDS:
            expected = ref[field]
            actual = getattr(got, field)
            if field == "descriptors":
                expected = set(filter(None, (expected or "").split(",")))
                actual = set(filter(None, (actual or "").split(",")))
            if expected != actual:
                diffs[field] = {"apple": sorted(expected) if isinstance(expected, set)
                                else expected,
                                "ours": sorted(actual) if isinstance(actual, set)
                                else actual}

        out["diffs"] = diffs
        out["match"] = not diffs
        out["apple_markers"] = ref["marker_count"]
        out["our_markers"] = info.beat_markers.marker_count
        out["apple_duration"] = round(ref["duration"], 4)
        out["our_duration"] = round(info.audio.duration or 0.0, 4)
        out["verify"] = [f"{sev}: {msg}" for sev, msg in verify_apple_loop(info)]
        return out


def check_roundtrip(refs, workdir: Path, lossy: bool):
    results = []
    for ref in refs:
        if ref.get("error"):
            continue
        results.append(roundtrip_one(ref, workdir, lossy))

    usable = [r for r in results if "error" not in r]
    matched = [r for r in usable if r["match"]]
    field_failures = Counter()
    for r in usable:
        for field in r.get("diffs", {}):
            field_failures[field] += 1

    return {
        "attempted": len(results),
        "usable": len(usable),
        "errors": [r for r in results if "error" in r][:20],
        "exact_metadata_match": len(matched),
        "exact_metadata_match_pct": (round(100.0 * len(matched) / len(usable), 2)
                                     if usable else None),
        "field_mismatch_counts": dict(field_failures.most_common()),
        "verify_failures": [r for r in usable
                            if any(v.startswith("error") for v in r["verify"])][:20],
        "examples": [r for r in usable if not r["match"]][:25],
    }


# ---------------------------------------------------------------------------
# Calibration: the distributions the verifier's thresholds should come from
# ---------------------------------------------------------------------------

def percentiles(values, points=(0, 0.1, 1, 5, 50, 95, 99, 99.9, 100)):
    if not values:
        return {}
    ordered = sorted(values)
    out = {}
    for p in points:
        idx = min(len(ordered) - 1, int(round(p / 100.0 * (len(ordered) - 1))))
        out[f"p{p}"] = round(ordered[idx], 4)
    return out


def check_calibration(refs):
    """What Apple's own loops actually look like on each thing we check.

    Every threshold in `verify_apple_loop` was a guess. These are the numbers
    it should have been set from.
    """
    tempos, marker_end_ratio = [], []
    markers_no_beats = beats_no_markers = no_metadata = 0
    has_spotlight_genre = 0
    tonal_percussion = 0

    for r in refs:
        beats, duration = r.get("beat_count") or 0, r.get("duration") or 0.0
        if beats > 0 and duration > 0:
            tempos.append(beats * 60.0 / duration)

        if r.get("last_marker") and r.get("num_frames"):
            marker_end_ratio.append(r["last_marker"] / r["num_frames"])

        if r.get("marker_count") and beats <= 0:
            markers_no_beats += 1
        if beats > 0 and not r.get("marker_count"):
            beats_no_markers += 1
        if not any((r.get("category"), r.get("genre"), beats,
                    r.get("key_signature"), r.get("descriptors"))):
            no_metadata += 1
        if r.get("spotlight_genre"):
            has_spotlight_genre += 1
        if r.get("category") in ("Drums", "Percussion") and r.get("key_signature"):
            tonal_percussion += 1

    n = len(refs)
    return {
        "loops": n,
        "derived_tempo": percentiles(tempos),
        "last_marker_over_num_frames": percentiles(marker_end_ratio),
        "markers_but_no_beat_count": markers_no_beats,
        "beat_count_but_no_markers": beats_no_markers,
        "no_metadata_at_all": no_metadata,
        "has_spotlight_genre": has_spotlight_genre,
        "has_spotlight_genre_pct": round(100.0 * has_spotlight_genre / n, 2) if n else None,
        "percussion_with_a_key": tonal_percussion,
    }


def marker_profile(refs):
    """Marker density and spacing, for comparing our output against Apple's."""
    per_beat, min_gaps, sparser = [], [], 0
    under_10ms = total_gaps = 0

    for r in refs:
        beats, count = r.get("beat_count") or 0, r.get("marker_count") or 0
        positions = r.get("positions") or []
        sr = r.get("sample_rate") or 44100
        if beats <= 0 or count < 2 or len(positions) < 2:
            continue
        pos = sorted(set(positions))
        gaps = [(pos[i + 1] - pos[i]) / sr * 1000 for i in range(len(pos) - 1)]
        if not gaps:
            continue
        per_beat.append(count / beats)
        min_gaps.append(min(gaps))
        under_10ms += sum(1 for g in gaps if g < 10)
        total_gaps += len(gaps)
        if count < beats:
            sparser += 1

    if not per_beat:
        return {}
    min_gaps.sort()
    return {
        "loops": len(per_beat),
        "markers_per_beat_median": round(statistics.median(per_beat), 2),
        "fewer_markers_than_beats_pct": round(100.0 * sparser / len(per_beat), 1),
        "smallest_gap_p5_ms": round(min_gaps[len(min_gaps) // 20], 2),
        "gaps_under_10ms_pct": round(100.0 * under_10ms / total_gaps, 3),
    }


def print_marker_profile(label, m):
    if not m:
        print(f"{label:<34} (no comparable loops)")
        return
    print(f"{label:<34} loops {m['loops']:>5} | per beat {m['markers_per_beat_median']:>5} "
          f"| sparser than 1/beat {m['fewer_markers_than_beats_pct']:>5}% "
          f"| smallest gap p5 {m['smallest_gap_p5_ms']:>7} ms "
          f"| gaps under 10 ms {m['gaps_under_10ms_pct']:>6}%")


def print_calibration(c):
    _banner("CALIBRATION: what Apple's own loops actually do")
    print(f"Loops:                          {c['loops']:,}")
    print(f"\nDerived tempo (beats*60/duration) percentiles:")
    print(f"  {c['derived_tempo']}")
    print(f"\nLast beat marker as a fraction of the audio length:")
    print(f"  {c['last_marker_over_num_frames']}")
    print(f"\nMarkers but no beat count:      {c['markers_but_no_beat_count']:,}")
    print(f"Beat count but no markers:      {c['beat_count_but_no_markers']:,}")
    print(f"No metadata at all:             {c['no_metadata_at_all']:,}")
    print(f"Genre in the info chunk:        {c['has_spotlight_genre']:,} "
          f"({c['has_spotlight_genre_pct']}%)")
    print(f"Drums/Percussion with a key:    {c['percussion_with_a_key']:,}")


# ---------------------------------------------------------------------------
# Check 4: the verifier, judged against Apple's own loops
# ---------------------------------------------------------------------------

def verify_reference(path):
    """Run our own --verify checks over a file Apple authored.

    Apple's loops are the definition of correct, so every "error" this reports
    is a false positive in our checker, not a fault in the loop.
    """
    try:
        info = AppleDecoderPool.decoder().decode_file(Path(path))
    except Exception as exc:                                  # pragma: no cover
        return {"path": str(path), "error": str(exc)}
    return {"path": str(path),
            "findings": [[sev, msg] for sev, msg in verify_apple_loop(info)]}


def check_verifier(loops, workers: int):
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(verify_reference, [str(p) for p in loops],
                                chunksize=32))

    results = [r for r in results if "error" not in r]
    kinds = Counter()
    examples = defaultdict(list)
    clean = 0
    with_errors = 0

    for r in results:
        if not r["findings"]:
            clean += 1
        if any(sev == "error" for sev, _ in r["findings"]):
            with_errors += 1
        for sev, msg in r["findings"]:
            # Group by the shape of the message, not its numbers.
            key = (sev, msg.split(":")[0].split(" is ")[0][:70])
            kinds[key] += 1
            if len(examples[key]) < 3:
                examples[key].append(Path(r["path"]).name)

    return {
        "loops_checked": len(results),
        "clean": clean,
        "clean_pct": round(100.0 * clean / len(results), 2) if results else None,
        "with_at_least_one_error": with_errors,
        "false_positive_rate_pct": (round(100.0 * with_errors / len(results), 3)
                                    if results else None),
        "findings": [{"severity": sev, "message": msg, "count": n,
                      "examples": examples[(sev, msg)]}
                     for (sev, msg), n in kinds.most_common()],
    }


def print_verifier(v):
    _banner("VERIFIER: what our --verify says about Apple's OWN loops")
    print(f"Loops checked:           {v['loops_checked']:,}")
    print(f"Reported clean:          {v['clean']:,} ({v['clean_pct']}%)")
    print(f"Flagged with an ERROR:   {v['with_at_least_one_error']:,} "
          f"({v['false_positive_rate_pct']}%)  <- false positives by definition")
    for f in v["findings"]:
        print(f"\n  [{f['severity']:<7}] {f['count']:>7,}x  {f['message']}")
        print(f"            e.g. {', '.join(f['examples'])}")


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--mode",
                        choices=("vocabulary", "beatcount", "roundtrip",
                                 "verifier", "calibrate", "all"),
                        default="all")
    parser.add_argument("--limit", type=int,
                        help="Sample this many loops (deterministic)")
    parser.add_argument("--roundtrip-limit", type=int, default=150,
                        help="Round trips are slow; cap them separately")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--tail-beats", type=float, default=0.0,
                        help="Simulate up to this much decay past the final "
                             "beat when testing beat-count recovery. Apple's "
                             "own loops are trimmed exactly, so 0 proves only "
                             "that snapping is harmless.")
    parser.add_argument("--lossy", action="store_true",
                        help="Round-trip through AAC instead of ALAC")
    parser.add_argument("--compare-markers", type=Path, metavar="DIR",
                        help="A directory of converted .caf files to profile "
                             "against Apple's marker density and spacing")
    parser.add_argument("--json", type=Path, help="Write the full report here")
    args = parser.parse_args()

    if not args.library.exists():
        print(f"Error: no Apple Loops library at {args.library}", file=sys.stderr)
        sys.exit(1)

    loops = find_loops(args.library, args.limit)
    print(f"Apple Loops library: {args.library}")
    print(f"Loops selected: {len(loops)}"
          f"{f' (sampled from the full set)' if args.limit else ''}")
    print("Decoding...", flush=True)

    refs = collect(loops, args.workers)
    failed = [r for r in refs if r.get("error")]
    refs = [r for r in refs if not r.get("error")]
    print(f"Decoded {len(refs)} ({len(failed)} failed)\n")

    report = {"library": str(args.library), "decoded": len(refs),
              "decode_failures": len(failed)}

    if args.mode in ("vocabulary", "all"):
        report["vocabulary"] = check_vocabulary(refs)
        print_vocabulary(report["vocabulary"])

    if args.mode in ("beatcount", "all"):
        report["beat_counts"] = check_beat_counts(refs, args.tail_beats)
        print_beat_counts(report["beat_counts"])

    if args.mode in ("calibrate", "all"):
        report["calibration"] = check_calibration(refs)
        print_calibration(report["calibration"])

    if args.compare_markers:
        _banner("MARKER PROFILE: our placement against Apple's")
        ours = collect(find_loops(args.compare_markers, args.limit), args.workers)
        report["marker_profile"] = {
            "apple": marker_profile(refs),
            "ours": marker_profile([r for r in ours if not r.get("error")]),
        }
        print_marker_profile("APPLE (authored by Apple)", report["marker_profile"]["apple"])
        print_marker_profile("OURS", report["marker_profile"]["ours"])

    if args.mode in ("verifier", "all"):
        report["verifier"] = check_verifier(loops, args.workers)
        print_verifier(report["verifier"])

    if args.mode in ("roundtrip", "all"):
        subset = refs[:args.roundtrip_limit]
        print(f"\nRound-tripping {len(subset)} loops through "
              f"afconvert -> converter -> decoder...", flush=True)
        with tempfile.TemporaryDirectory() as workdir:
            report["roundtrip"] = check_roundtrip(subset, Path(workdir), args.lossy)
        print_roundtrip(report["roundtrip"])

    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
        print(f"\nFull report: {args.json}")


def _banner(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def print_vocabulary(v):
    _banner("VOCABULARY: what Apple actually ships vs our curated lists")
    for field in ("category", "subcategory", "genre", "descriptors"):
        d = v[field]
        print(f"\n{field}: {d['distinct_in_library']} distinct values in the library")
        missing = d["missing_from_our_list"]
        if missing:
            print(f"  MISSING from apple_loops_vocabulary.py ({len(missing)}):")
            for value, count in list(missing.items())[:40]:
                print(f"    {count:>7,}x  {value!r}")
        else:
            print("  every shipped value is in our list")
        unused = d["in_our_list_but_never_shipped"]
        if unused:
            print(f"  in our list but never shipped ({len(unused)}): "
                  f"{', '.join(unused[:20])}")

    print(f"\nkey_type values shipped: {v['key_type']}")
    print(f"time_signature values shipped: {v['time_signature']}")
    print(f"codecs shipped: {v['codec']}")


def print_beat_counts(b):
    _banner("BEAT COUNT: can we recover Apple's own beat count?")
    if b["tail_beats_simulated"]:
        print(f"Simulating up to {b['tail_beats_simulated']} beats of decay "
              f"past the final beat.")
    else:
        print("Using Apple's durations unmodified (their loops are trimmed "
              "exactly, so this only shows snapping does no harm).")
    print(f"Loops tested:            {b['loops_tested']:,}")
    print(f"Recovered by snapping:   {b['recovered']:,} ({b['recovered_pct']}%)")
    print(f"Recovered by rounding:   {b['naive_rounding_recovered']:,} "
          f"({b['naive_rounding_pct']}%)   <- the old behaviour")
    if b["error_distribution"]:
        print(f"Error distribution (ours - Apple): {b['error_distribution']}")
    for ex in b["examples"][:10]:
        print(f"  {Path(ex['path']).name}: Apple {ex['apple_beats']}, "
              f"ours {ex['ours']}, naive {ex['naive_rounding']} "
              f"({ex['duration']}s @ {ex['exact_tempo']} -> {ex['reported_tempo']} BPM)")


def print_roundtrip(r):
    _banner("ROUND TRIP: strip to WAV, rebuild, compare with the original")
    print(f"Attempted:               {r['attempted']}")
    print(f"Usable:                  {r['usable']}")
    print(f"Exact metadata match:    {r['exact_metadata_match']} "
          f"({r['exact_metadata_match_pct']}%)")
    if r["field_mismatch_counts"]:
        print(f"Field mismatches:        {r['field_mismatch_counts']}")
    if r["errors"]:
        print(f"Errors: {len(r['errors'])}")
        for e in r["errors"][:5]:
            print(f"  {e['name']}: {e['error']}")
    if r["verify_failures"]:
        print(f"\n--verify errors on our output: {len(r['verify_failures'])}")
        for f in r["verify_failures"][:5]:
            print(f"  {f['name']}: {f['verify']}")
    for ex in r["examples"][:10]:
        print(f"\n  {ex['name']}")
        for field, d in ex["diffs"].items():
            print(f"    {field}: apple={d['apple']!r} ours={d['ours']!r}")


if __name__ == "__main__":
    main()
