#!/usr/bin/env python3
"""Measure TypeSafe's Jev against this toolkit's own labelling rules.

Two modes:

**apple** -- ground truth. Apple's shipped loops carry a human-written name and
Apple's own category, subcategory and genre (export them on macOS with
`export-apple-loop-labels.py`). Sample them evenly by category, then score the
filename extractor (`MetadataExtractor`, what the converter uses for anything
that is not a Splice download) and Jev on the same names. Jev is asked twice,
the second time with every option list reversed, to measure its documented
lean towards the first option.

**splice** -- no ground truth, a review list. For every sample in a Splice
`sounds.db`, compare the converter's answer (`splice_sample_to_metadata`) with
Jev's and write the disagreements, most confident first, for a person to judge.

Every response is cached by a hash of its request, so re-running the analysis
costs nothing. Needs TYPESAFE_API_KEY.

Usage:
    ./jev-benchmark.py apple --labels apple-loop-labels.json --per-category 50 \\
        --cache jev-cache.jsonl --out apple-results.json
    ./jev-benchmark.py splice --splice-db sounds.db --cache jev-cache.jsonl \\
        --out splice-results.json --tsv splice-disagreements.tsv
"""

import argparse
import hashlib
import json
import random
import re
import statistics
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from convert_to_apple_loops import (  # noqa: E402
    APPLE_FALLBACK_SUBCATEGORY,
    MetadataExtractor,
    splice_sample_to_metadata,
)
from jev_mapping import (  # noqa: E402
    JevClient, build_questions, category_options, load_config, resolve)
from splice_db import SpliceLibrary  # noqa: E402

CONFIDENCE_BINS = (0.0, 0.5, 0.7, 0.85, 0.95, 1.0001)
SHIPPED_VOCABULARY = Path(__file__).resolve().parent.parent / "reference" / "apple-loops-shipped-vocabulary.json"


class CachedJev:
    """JevClient behind a JSONL cache keyed by the exact request."""

    def __init__(self, client: JevClient, cache_path: Path):
        self.client, self.path, self.lock = client, cache_path, threading.Lock()
        self.cache, self.calls = {}, 0
        if cache_path.exists():
            for line in cache_path.read_text().splitlines():
                entry = json.loads(line)
                self.cache[entry["key"]] = entry["response"]

    def ask(self, state, questions):
        # Never sort_keys: option order is part of the question (the reversed
        # run exists to vary it), and sorting made both orders one cache entry.
        key = hashlib.sha256(json.dumps(
            [self.client.model, state, questions]).encode()).hexdigest()
        if key in self.cache:
            return self.cache[key], False
        response = self.client.ask(state, questions)
        with self.lock:
            self.cache[key] = response
            self.calls += 1
            with open(self.path, "a") as fh:
                fh.write(json.dumps({"key": key, "response": response}) + "\n")
        return response, True


def name_stem(name: str) -> str:
    """'Groovy Electric Bass 31' -> 'Groovy Electric Bass'."""
    return re.sub(r"[\s_-]*\d+$", "", name).strip() or name


def pack_name(pack: str) -> str:
    """'09 Disco Funk' -> 'Disco Funk'; Apple numbers its packs."""
    return re.sub(r"^\d+\s+", "", pack)


def ask_all(jev, states, config, source, reverse, workers):
    questions, sub_ids = build_questions(config, source, reverse=reverse)

    def one(state):
        response, fresh = jev.ask(state, questions)
        out = resolve(response, sub_ids)
        out["elapsed"] = response.get("elapsed") if fresh else None
        out["fresh"] = fresh
        return out

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, states))


def pct(numerator, denominator):
    return round(100.0 * numerator / denominator, 1) if denominator else None


def usage_summary(results, price):
    fresh = [r for r in results if r["fresh"]]
    tokens = sum(r["input_tokens"] for r in results)
    latencies = sorted(r["elapsed"] for r in fresh if r["elapsed"] is not None)
    return {
        "calls": len(results),
        "fresh_calls": len(fresh),
        "input_tokens": tokens,
        "tokens_per_call": round(tokens / len(results)) if results else 0,
        "usd": round(tokens * price / 1e6, 4),
        "latency_mean_s": round(statistics.mean(latencies), 3) if latencies else None,
        "latency_p95_s": round(latencies[int(0.95 * (len(latencies) - 1))], 3) if latencies else None,
        "models": dict(Counter(r["model"] for r in results)),
    }


# ---------------------------------------------------------------------------
# apple: scored against Apple's labels
# ---------------------------------------------------------------------------

def sample_apple(records, per_category, seed, categories):
    """One loop per distinct name, then up to `per_category` per Apple category.

    Only `categories` -- the ones the model is offered -- are sampled, so a loop
    is never scored against an answer that could not be given.
    """
    seen, by_category = set(), defaultdict(list)
    for r in records:
        stem = name_stem(r["name"])
        if r["category"] not in categories or stem.lower() in seen:
            continue
        seen.add(stem.lower())
        by_category[r["category"]].append({**r, "stem": stem})
    rng, chosen = random.Random(seed), []
    for category in sorted(by_category):
        group = by_category[category]
        rng.shuffle(group)
        chosen.extend(group[:per_category])
    return chosen


def score(items, method):
    """Accuracy of one method's labels against Apple's."""
    def acc(field_pairs, keep):
        rows = [i for i in items if keep(i)]
        right = sum(all(i[method][f] == i["truth"][t] for f, t in field_pairs) for i in rows)
        return {"n": len(rows), "correct": right, "pct": pct(right, len(rows))}
    return {
        "category": acc([("category", "category")], lambda i: True),
        "category_and_subcategory": acc(
            [("category", "category"), ("subcategory", "subcategory")],
            lambda i: bool(i["truth"]["subcategory"])),
        "genre": acc([("genre", "genre")], lambda i: bool(i["truth"]["genre"])),
    }


def calibration(items, method="jev"):
    bins = []
    for lo, hi in zip(CONFIDENCE_BINS, CONFIDENCE_BINS[1:]):
        rows = [i for i in items if lo <= i[method]["category_confidence"] < hi]
        right = sum(i[method]["category"] == i["truth"]["category"] for i in rows)
        bins.append({"confidence": f"{lo:.2f}-{min(hi, 1):.2f}", "n": len(rows),
                     "category_pct": pct(right, len(rows))})
    return bins


def run_apple(args, jev, config):
    data = json.loads(Path(args.labels).read_text())
    items = sample_apple(data["records"], args.per_category, args.seed,
                         set(category_options(config)))
    extractor = MetadataExtractor()
    for item in items:
        item["truth"] = {k: item[k] for k in ("category", "subcategory", "genre")}
        category, subcategory = extractor.extract_instrument(item["name"])
        item["baseline"] = {
            "category": category, "subcategory": subcategory,
            "genre": extractor.extract_genre(item["name"], item["pack"]),
            "abstained": (category, subcategory) == ("Other Instrument", APPLE_FALLBACK_SUBCATEGORY),
        }

    states = [{"name": i["stem"], "pack": pack_name(i["pack"])} for i in items]
    forward = ask_all(jev, states, config, "apple", False, args.workers)
    for item, answer in zip(items, forward):
        item["jev"] = answer
    reversed_ = []
    if not args.no_reverse:
        reversed_ = ask_all(jev, states, config, "apple", True, args.workers)
        for item, answer in zip(items, reversed_):
            item["jev_reversed"] = answer

    # Rules first, Jev only where the rules found no keyword.
    for item in items:
        item["hybrid"] = item["jev"] if item["baseline"]["abstained"] else item["baseline"]

    answered = [i for i in items if not i["baseline"]["abstained"]]
    per_category = {}
    for category in sorted({i["truth"]["category"] for i in items}):
        rows = [i for i in items if i["truth"]["category"] == category]
        per_category[category] = {
            "n": len(rows),
            "baseline_pct": pct(sum(i["baseline"]["category"] == category for i in rows), len(rows)),
            "jev_pct": pct(sum(i["jev"]["category"] == category for i in rows), len(rows)),
        }
    # The sample is even across categories; Apple's library is not. Weight each
    # category's accuracy by how many loops Apple ships in it.
    shipped = json.loads(SHIPPED_VOCABULARY.read_text())["shipped"]["categories"]
    weight = {c: shipped.get(c, 0) for c in per_category}

    def weighted(key):
        total = sum(weight.values())
        return round(sum(weight[c] * (per_category[c][key] or 0) for c in per_category) / total, 1)
    confusions = Counter((i["truth"]["category"], i["jev"]["category"])
                         for i in items if i["jev"]["category"] != i["truth"]["category"])

    summary = {
        "mode": "apple",
        "loops": len(items),
        "per_category_cap": args.per_category,
        "seed": args.seed,
        "baseline": score(items, "baseline"),
        "baseline_abstained": {"n": len(items) - len(answered),
                               "pct": pct(len(items) - len(answered), len(items))},
        "baseline_when_it_answered": score(answered, "baseline"),
        "jev_on_the_same_loops": score(answered, "jev"),
        "jev": score(items, "jev"),
        "hybrid_rules_then_jev": score(items, "hybrid"),
        "category_weighted_by_apple_library": {
            "baseline_pct": weighted("baseline_pct"), "jev_pct": weighted("jev_pct")},
        "jev_calibration": calibration(items),
        "per_category": per_category,
        "jev_top_confusions": [
            {"apple": a, "jev": b, "n": n} for (a, b), n in confusions.most_common(15)],
        "usage": usage_summary(forward + reversed_, config["usd_per_million_input_tokens"]),
    }
    if reversed_:
        summary["jev_reversed"] = score(items, "jev_reversed")
        summary["order_flips"] = {
            field: {"n": sum(i["jev"][field] != i["jev_reversed"][field] for i in items),
                    "pct": pct(sum(i["jev"][field] != i["jev_reversed"][field] for i in items), len(items))}
            for field in ("category", "subcategory", "genre")}
    return summary, items


# ---------------------------------------------------------------------------
# splice: a review list against the converter's own answers
# ---------------------------------------------------------------------------

def run_splice(args, jev, config):
    with SpliceLibrary(args.splice_db) as library:
        samples = list(library.samples())
    if args.limit:
        samples = samples[:args.limit]

    items, states = [], []
    for sample in samples:
        current = splice_sample_to_metadata(sample)
        items.append({
            "filename": sample.filename,
            "tags": sample.tag_list,
            "sample_type": sample.sample_type or "unknown",
            "splice_genre": sample.genre or "",
            "current": {"category": current.category, "subcategory": current.subcategory,
                        "genre": current.genre},
        })
        states.append({"filename": Path(sample.filename).stem,
                       "tags": sample.tag_list,
                       "sample_type": sample.sample_type or "unknown"})

    for item, answer in zip(items, ask_all(jev, states, config, "splice", False, args.workers)):
        item["jev"] = answer

    def agree(fields):
        n = sum(all(i["jev"][f] == i["current"][f] for f in fields) for i in items)
        return {"n": n, "pct": pct(n, len(items))}

    disagreements = sorted(
        (i for i in items if i["jev"]["category"] != i["current"]["category"]),
        key=lambda i: -i["jev"]["category_confidence"])
    if args.tsv:
        with open(args.tsv, "w") as fh:
            fh.write("jev_confidence\tcurrent\tjev\tsample_type\ttags\tfilename\n")
            for i in disagreements:
                fh.write("\t".join([
                    f"{i['jev']['category_confidence']:.2f}",
                    f"{i['current']['category']} / {i['current']['subcategory']}",
                    f"{i['jev']['category']} / {i['jev']['subcategory']}",
                    i["sample_type"], ",".join(i["tags"]), i["filename"]]) + "\n")

    summary = {
        "mode": "splice",
        "samples": len(items),
        "agree_category": agree(["category"]),
        "agree_category_and_subcategory": agree(["category", "subcategory"]),
        "agree_genre": agree(["genre"]),
        "category_disagreements": len(disagreements),
        "disagreement_pairs": [
            {"current": a, "jev": b, "n": n} for (a, b), n in Counter(
                (i["current"]["category"], i["jev"]["category"]) for i in disagreements
            ).most_common(15)],
        "usage": usage_summary([i["jev"] for i in items], config["usd_per_million_input_tokens"]),
    }
    return summary, items


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("apple", "splice"):
        p = sub.add_parser(name)
        p.add_argument("--cache", type=Path, required=True)
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--workers", type=int, default=8)
        p.add_argument("--model", help="Defaults to the model pinned in jev-questions.json")
    sub.choices["apple"].add_argument("--labels", type=Path, required=True)
    sub.choices["apple"].add_argument("--per-category", type=int, default=50)
    sub.choices["apple"].add_argument("--seed", type=int, default=1507)
    sub.choices["apple"].add_argument("--no-reverse", action="store_true")
    sub.choices["splice"].add_argument("--splice-db", type=Path, required=True)
    sub.choices["splice"].add_argument("--limit", type=int)
    sub.choices["splice"].add_argument("--tsv", type=Path)
    args = parser.parse_args()

    config = load_config()
    jev = CachedJev(JevClient(model=args.model or config["model"]), args.cache)
    run = run_apple if args.mode == "apple" else run_splice
    summary, items = run(args, jev, config)
    args.out.write_text(json.dumps({"summary": summary, "items": items}, indent=1))
    print(json.dumps(summary, indent=2))
    print(f"\n{jev.calls} new Jev calls; results in {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
