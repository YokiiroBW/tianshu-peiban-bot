"""Bounded regulation and hysteresis over appraisals; personality remains authored config."""


def regulate(value, previous="steady"):
    valence, stress = value["valence"], value["stress"]
    if previous == "strained":
        mode = "strained" if stress > .35 or valence < -.15 else "steady"
    elif previous == "engaged":
        mode = "engaged" if valence > .15 and stress < .55 else "steady"
    elif stress > .65 or valence < -.35:
        mode = "strained"
    elif valence > .35 and value["arousal"] > .15:
        mode = "engaged"
    else:
        mode = "steady"
    if mode == "strained" and value.get("control", .5) >= .55:
        expression = "Keep the authored voice; speak steadily and offer one practical step when relevant."
        activity = "Prefer a manageable step on a relevant goal; preserve every explicit commitment."
    elif mode == "strained":
        expression = "Keep the authored voice; use a gentler pace without guilt, pressure or emotional demands."
        activity = "Restorative reading, reflection or quiet creative work may fit; do not invent a failure or cancel a promise."
    elif mode == "engaged":
        expression = "A little more lively curiosity may fit the authored style; keep boundaries and factual accuracy."
        activity = "Exploration or creative work may fit genuine interests, within current time and resources."
    else:
        expression = "Use the authored character's natural expression and the current person's explicit preference."
        activity = "Choose freely from current interests, actual open matters and commitments."
    return {"mode": mode, "expression_direction": expression, "activity_bias": activity,
            "intensity": round(min(.7, max(abs(valence), stress) * (.5 + .5 * abs(value.get("goal_relevance", 0)))), 4)}
