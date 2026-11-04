"""Project only recorded input selection and submission facts for a dialogue turn."""

from collections.abc import Mapping


def input_context_status(inputs, selections):
    """Keep merged inputs distinct and do not infer how a response used them."""
    result = []
    for item in inputs:
        attempts = []
        for selection in selections:
            matches = []
            for entry in selection.get("selection") or ():
                if entry.get("kind") != "conversation_unit":
                    continue
                for record in entry.get("records") or ():
                    if (record.get("speaker") == "user"
                            and record.get("turn_id") == item["input_id"]
                            and record.get("event_id") == item["event_id"]
                            and record.get("revision") == item["event_revision"]):
                        matches.append(entry.get("selected") is True)
            if not matches:
                continue
            receipt = selection.get("model_receipt") or {}
            dispatch = receipt.get("dispatch")
            if dispatch not in {"submitted", "not_started", "unknown"}:
                dispatch = "not_recorded"
            attempts.append({"attempt_id": selection["attempt_id"], "round": selection["round"],
                             "selected": any(matches), "dispatch": dispatch})
        state = ("selected" if any(attempt["selected"] for attempt in attempts)
                 else "not_selected" if attempts else "not_recorded")
        result.append({**item, "context": {"selection": state, "attempts": attempts},
                       "reply_compliance": "not_recorded"})
    return result


def attach_learning_status(inputs, rows):
    """Accept only the owner's status for the exact original input identity."""
    expected = {item["input_id"]: item for item in inputs}
    observed = {}
    if isinstance(rows, (str, bytes, Mapping)):
        raise ValueError("invalid_input_learning_status")
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("invalid_input_learning_status")
        identifier = row.get("input_id")
        original = expected.get(identifier)
        if (not original or identifier in observed or row.get("event_id") != original["event_id"]
                or row.get("event_revision") != original["event_revision"]
                or row.get("state") not in {"pending", "failed", "reviewed", "unavailable", "not_recorded"}
                or row.get("revision_state") not in {"applied", "unresolved", "not_recorded"}
                or (row.get("revision_state") == "applied" and not row.get("revision_evidence"))):
            raise ValueError("invalid_input_learning_status")
        observed[identifier] = {key: value for key, value in row.items()
                                if key not in {"input_id", "event_id", "event_revision"}}
    for item in inputs:
        item["learning"] = observed.get(item["input_id"],
            {"state": "not_recorded", "revision_state": "not_recorded"})
