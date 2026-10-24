"""jev_mapping builds Jev questions from Apple's vocabulary and reads the answers.

No network: the client takes an injectable opener and sleep.
"""

import io
import json
import urllib.error

import pytest

from apple_loops_vocabulary import APPLE_CATEGORY_SUBCATEGORIES, APPLE_GENRES
from jev_mapping import JevClient, build_questions, category_options, load_config, resolve


@pytest.fixture
def config():
    return load_config()


def test_every_offered_option_is_apples(config):
    questions, sub_ids = build_questions(config, "apple")
    assert set(questions["category"]["criteria"]) <= set(APPLE_CATEGORY_SUBCATEGORIES)
    assert set(questions["genre"]["criteria"]) <= set(APPLE_GENRES)
    for category, qid in sub_ids.items():
        assert set(questions[qid]["criteria"]) == set(APPLE_CATEGORY_SUBCATEGORIES[category])


def test_excluded_and_empty_categories_are_not_offered(config):
    offered = category_options(config)
    assert "Other" not in offered                     # ships with no subcategory
    for excluded in config["category"]["exclude"]:
        assert excluded not in offered


def test_described_categories_are_offered(config):
    """A description for a category that is never offered is dead config."""
    assert set(config["category"]["criteria"]) == set(category_options(config))


def test_reverse_reverses_every_choice(config):
    forward, _ = build_questions(config, "apple")
    backward, _ = build_questions(config, "apple", reverse=True)
    assert forward.keys() == backward.keys()
    for qid in forward:
        assert list(backward[qid]["criteria"]) == list(reversed(list(forward[qid]["criteria"])))


def test_instructions_point_at_the_sources_state_fields(config):
    apple, _ = build_questions(config, "apple")
    splice, _ = build_questions(config, "splice")
    assert "`name`" in apple["category"]["instructions"]
    assert "`tags`" in splice["category"]["instructions"]


def _answer(choice, confidence=0.9):
    return {"type": "choice", "choice": choice, "confidence": confidence,
            "probabilities": {choice: confidence}}


def test_resolve_takes_the_subcategory_of_the_chosen_category(config):
    questions, sub_ids = build_questions(config, "apple")
    answers = {qid: _answer("unused") for qid in questions}
    answers["category"] = _answer("Bass", 0.97)
    answers[sub_ids["Bass"]] = _answer("Synthetic Bass", 0.8)
    answers["genre"] = _answer("Hip Hop", 0.6)
    out = resolve({"model": "jev-1.13.0", "answers": answers,
                   "usage": {"input_tokens": 2630}}, sub_ids)
    assert (out["category"], out["subcategory"], out["genre"]) == ("Bass", "Synthetic Bass", "Hip Hop")
    assert out["category_confidence"] == 0.97 and out["input_tokens"] == 2630


def test_resolve_fills_a_single_subcategory_without_asking(config):
    _, sub_ids = build_questions(config, "apple")
    assert "Mixed" not in sub_ids                     # one subcategory: nothing to ask
    out = resolve({"answers": {"category": _answer("Mixed"), "genre": _answer("Funk")}}, sub_ids)
    assert out["subcategory"] == "Other Instrument"


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_client_retries_overload_then_succeeds():
    calls, slept = [], []

    def opener(request, timeout):
        calls.append(json.loads(request.data))
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 529, "Overloaded",
                                         {"retry-after": "2"}, io.BytesIO(b"{}"))
        return _Response(json.dumps({"answers": {}, "model": "jev-1.13.0"}).encode())

    client = JevClient(api_key="k", model="jev-1.13.0", opener=opener, sleep=slept.append)
    result = client.ask({"name": "x"}, {})
    assert result["model"] == "jev-1.13.0" and len(calls) == 2
    assert slept == [2.0]                             # honours retry-after
    assert calls[0]["model"] == "jev-1.13.0"


def test_client_does_not_retry_a_bad_request():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 422, "Unprocessable",
                                     {}, io.BytesIO(b'{"detail":"bad"}'))

    client = JevClient(api_key="k", opener=opener, sleep=lambda s: None)
    with pytest.raises(RuntimeError, match="422"):
        client.ask({}, {})


def test_client_needs_a_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TYPESAFE_API_KEY"):
        JevClient()
