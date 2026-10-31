"""Explainable content value and interruption thresholds, without models or I/O."""

import hashlib
import math
import unicodedata

from tianshu.conversation.codec import signature as digest

WEIGHTS = dict(
    novelty=0.30,
    interest_fit=0.25,
    affect_pull=0.20,
    recency_need=0.15,
    social_cost=-0.20,
    repetition=-0.15,
)


def normalized(text):
    return "".join(
        char for char in unicodedata.normalize("NFKC", text).casefold() if char.isalnum()
    )


def fingerprint(text, originals=()):
    value = normalized(text)[:4000]
    shingles = sorted(
        {
            hashlib.blake2b(value[i : i + 3].encode(), digest_size=8).hexdigest()
            for i in range(max(0, len(value) - 2))
        }
    )[:64]
    return dict(exact=digest(value), shingles=shingles, originals=sorted(set(originals)))


def similarity(first, second):
    if first["exact"] == second["exact"] or set(first["originals"]) & set(second["originals"]):
        return 1.0
    left, right = set(first["shingles"]), set(second["shingles"])
    return len(left & right) / max(1, len(left | right))


def unit(value):
    if type(value) not in {int, float} or not math.isfinite(value):
        raise ValueError("Salience needs finite numeric inputs")
    return max(0.0, min(1.0, value))


def interest_fit(text, interests):
    value = normalized(text)
    matches = []
    for item in interests:
        topic = normalized(item["topic"])
        if topic and topic in value:
            matches.append(unit(item["strength"]))
    return max(matches, default=0.0)


def thresholds(responsiveness=0.5):
    # Responsiveness changes willingness to interrupt, never content ranking.
    return dict(draft=0.18, engage=round(0.40 + 0.20 * (0.5 - unit(responsiveness)), 6))


def evaluate(inputs, responsiveness=0.5):
    values = {key: unit(inputs[key]) for key in WEIGHTS}
    support = [key for key, weight in WEIGHTS.items() if weight > 0 and values[key] > 0]
    against = [key for key, weight in WEIGHTS.items() if weight < 0 and values[key] > 0]
    # Reasons are assembled before arithmetic; neither a model nor a high score can
    # erase a contrary fact such as repetition or an already active conversation.
    reasons = dict(
        support=support or ["little_support"], against=against or ["no_current_counterevidence"]
    )
    parts = {key: round(weight * values[key], 6) for key, weight in WEIGHTS.items()}
    return dict(
        inputs=values,
        components=parts,
        reasons=reasons,
        score=round(sum(parts.values()), 6),
        thresholds=thresholds(responsiveness),
    )
