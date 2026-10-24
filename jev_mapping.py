"""Ask TypeSafe's Jev model for Apple Loop labels, as a second opinion.

Jev (https://docs.typesafe.ai) does not generate text: it takes a `state` and
typed questions and returns one option from a fixed set with a probability per
option. That suits Apple's closed vocabularies, which every guess here must
land inside.

This is never on the conversion path. The converter reads its tables and
makes no network call; this module exists to measure those tables against
Apple's own labels and to propose changes to them for a person to review.

One call per item asks everything at once (TypeSafe's "speculative fan-out"):
the category, the genre, and the subcategory *as if* each category were the
answer, since a subcategory only means something within its category. The
code keeps the subcategory answer that belongs to the chosen category. Asking
106 category/subcategory pairs as one flat choice would be a single question
with far more options, and Jev leans towards whichever option comes first.

Question wording lives in jev-questions.json; the option lists come from
apple_loops_vocabulary.py. Needs TYPESAFE_API_KEY in the environment.
Standard library only, so it runs on the stock macOS Python.
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from apple_loops_vocabulary import APPLE_CATEGORY_SUBCATEGORIES, APPLE_GENRES

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
QUESTIONS_PATH = Path(__file__).resolve().with_name("jev-questions.json")
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 529})


def load_config(path: Path = QUESTIONS_PATH) -> dict:
    with open(path) as fh:
        return json.load(fh)


def category_options(config: dict) -> List[str]:
    """Apple categories that can be asked about.

    Those with any subcategory (`Other` ships on two loops with none), less the
    config's `exclude`: categories this toolkit never writes, which would only
    give the model a literal distinction Apple itself barely uses.
    """
    excluded = set(config["category"].get("exclude", ()))
    return [c for c, subs in APPLE_CATEGORY_SUBCATEGORIES.items()
            if subs and c not in excluded]


def _ordered(options: List[str], reverse: bool) -> List[str]:
    return list(reversed(options)) if reverse else list(options)


def _choice(instructions: str, options: List[str], descriptions: dict) -> dict:
    return {
        "type": "choice",
        "instructions": instructions,
        "criteria": {o: descriptions.get(o) for o in options},
    }


def build_questions(config: dict, source: str,
                    reverse: bool = False) -> Tuple[dict, Dict[str, str]]:
    """The questions for one item, and which question holds each category's subcategory.

    `reverse` lists every choice's options in the opposite order. Running both
    orders and comparing answers measures Jev's documented lean towards the
    first option.
    """
    subject = config["subjects"][source]
    categories = category_options(config)
    questions = {
        "category": _choice(
            config["category"]["instructions"].format(subject=subject),
            _ordered(categories, reverse), config["category"]["criteria"]),
    }

    sub_ids = {}
    for i, category in enumerate(categories):
        subs = sorted(APPLE_CATEGORY_SUBCATEGORIES[category])
        if len(subs) < 2:
            continue                    # nothing to choose between
        qid = f"subcategory_{i}"
        sub_ids[category] = qid
        questions[qid] = _choice(
            config["subcategory"]["instructions"].format(
                subject=subject, category=category),
            _ordered(subs, reverse), config["subcategory"]["criteria"])

    genres = [g for g in APPLE_GENRES if g not in config["genre"]["exclude"]]
    questions["genre"] = _choice(
        config["genre"]["instructions"].format(subject=subject),
        _ordered(genres, reverse), config["genre"]["criteria"])
    return questions, sub_ids


def resolve(response: dict, sub_ids: Dict[str, str]) -> dict:
    """Reduce a response to the labels for this item, with confidences."""
    answers = response["answers"]
    category = answers["category"]["choice"]
    sub_q = sub_ids.get(category)
    if sub_q:
        subcategory = answers[sub_q]["choice"]
        sub_conf = answers[sub_q]["confidence"]
    else:
        only = sorted(APPLE_CATEGORY_SUBCATEGORIES[category])
        subcategory, sub_conf = only[0], 1.0
    return {
        "category": category,
        "category_confidence": answers["category"]["confidence"],
        "category_probabilities": answers["category"]["probabilities"],
        "subcategory": subcategory,
        "subcategory_confidence": sub_conf,
        "genre": answers["genre"]["choice"],
        "genre_confidence": answers["genre"]["confidence"],
        "model": response.get("model"),
        "input_tokens": response.get("usage", {}).get("input_tokens", 0),
    }


class JevClient:
    """A minimal client for POST /v1/systemone with backoff on overload."""

    def __init__(self, api_key: Optional[str] = None, model: str = "jev-latest",
                 endpoint: str = ENDPOINT, timeout: float = 60, retries: int = 6,
                 opener: Callable = urllib.request.urlopen,
                 sleep: Callable[[float], None] = time.sleep):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not self.api_key:
            raise RuntimeError("Set TYPESAFE_API_KEY to call Jev")
        self.model, self.endpoint, self.timeout = model, endpoint, timeout
        self.retries, self._open, self._sleep = retries, opener, sleep

    def ask(self, state, questions: dict) -> dict:
        body = json.dumps({"state": state, "model": self.model,
                           "questions": questions}).encode()
        delay = 1.0
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(self.endpoint, data=body, headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            })
            start = time.monotonic()
            try:
                with self._open(request, timeout=self.timeout) as resp:
                    result = json.loads(resp.read())
                result["elapsed"] = time.monotonic() - start
                return result
            except urllib.error.HTTPError as err:
                if err.code not in RETRY_STATUSES or attempt == self.retries:
                    detail = err.read().decode(errors="replace")[:500]
                    raise RuntimeError(f"Jev HTTP {err.code}: {detail}") from None
                wait = float(err.headers.get("retry-after") or delay)
            except urllib.error.URLError:
                if attempt == self.retries:
                    raise
                wait = delay
            self._sleep(wait)
            delay = min(delay * 2, 30.0)
        raise AssertionError("unreachable")
