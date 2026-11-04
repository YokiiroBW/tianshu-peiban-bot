"""A read-only projection of durable dialogue stages and real receipts."""


def _stage_state(receipts):
    executions = {item.get("execution", "unknown") for item in receipts}
    effects = {item.get("effect", "unknown") for item in receipts}
    if executions & {"pending", "running", "queued"}:
        return "pending"
    if "unknown" in executions or "unknown" in effects:
        return "unknown"
    if executions & {"failed", "cancelled"}:
        return "failed"
    return "succeeded" if executions == {"succeeded"} else "unknown"


def dialogue_flow(turn, selections, links, expression, plan):
    rounds = []
    for entry in selections:
        model_receipt = entry.get("model_receipt")
        result = entry.get("model_result")
        number = entry["round"]
        accepted = ({"state": "accepted", "status": model_receipt.get("status"),
                     "dispatch": model_receipt.get("dispatch")}
                    if model_receipt else {"state": "pending"})
        if result is not None and model_receipt and model_receipt.get("status") == "succeeded":
            parsed = {"state": "parsed", "completion": (result.get("completion") or {}).get("state", "unknown"),
                      "part_kinds": [part.get("kind") for part in result.get("parts", ())]}
        elif model_receipt and model_receipt.get("status") in {"failed", "cancelled"}:
            parsed = {"state": "not_applicable", "reason": model_receipt.get("error_code")}
        elif model_receipt and model_receipt.get("status") == "succeeded":
            parsed = {"state": "unknown", "reason": "result_not_available"}
        else:
            parsed = {"state": "pending"}
        round_links = [link for link in links if link["call_id"].startswith(f"{number}:")]
        if round_links:
            tool_receipts = []
            for link in round_links:
                actual = link.get("result") or {}
                tool_receipts.append({"action_id": link["action_id"], "tool": link["tool_name"],
                    "execution": actual.get("execution", link.get("state", "unknown")),
                    "effect": actual.get("effect", "unknown"), "delivery": actual.get("delivery", "unknown"),
                    "error_code": actual.get("error_code")})
            applied = {"state": _stage_state(tool_receipts), "tools": tool_receipts}
        elif result is not None and not any(part.get("kind") == "tool_call" for part in result.get("parts", ())):
            applied = {"state": "not_applicable", "tools": []}
        elif parsed["state"] == "not_applicable":
            applied = {"state": "not_applicable", "tools": []}
        else:
            applied = {"state": "pending", "tools": []}
        is_final = number == turn["round"] and turn.get("expression_id") is not None
        if is_final:
            expression_stage = {"state": (plan or expression or {}).get("state", "pending"),
                                "expression_id": turn["expression_id"]}
            actual = (expression or {}).get("receipt") or (plan or {}).get("receipt")
            if actual:
                delivered = {"state": actual.get("delivery", "unknown"), "execution": actual.get("execution"),
                             "effect": actual.get("effect"), "message_ids": (actual.get("result") or {}).get("message_ids", []),
                             "delivery_progress": (actual.get("result") or {}).get("delivery_progress"),
                             "error_code": actual.get("error_code")}
            else:
                state = "unknown" if expression_stage["state"] in {"unknown", "delivery_unknown"} else "pending"
                delivered = {"state": state}
        elif result is not None and (round_links or parsed["state"] == "not_applicable" or
                                     str(turn.get("error_code") or "").startswith("model_late_") or
                                     turn.get("state") in {"completed", "failed", "cancelled"}):
            expression_stage = {"state": "not_applicable"}
            delivered = {"state": "not_applicable"}
        elif number < turn["round"] or parsed["state"] == "not_applicable":
            expression_stage = {"state": "not_applicable"}
            delivered = {"state": "not_applicable"}
        else:
            expression_stage = {"state": "pending"}
            delivered = {"state": "not_applicable"}
        rounds.append({"round": number, "attempt_id": entry["attempt_id"], "accepted": accepted,
                       "parsed": parsed, "applied": applied, "expression": expression_stage,
                       "delivered": delivered})
    return {"input": {"state": "accepted", "input_id": turn["turn_id"], "event_id": turn["event_id"],
                      "revision": turn["event_revision"], "received_at": turn["received_at"]},
            "rounds": rounds, "coverage": "recorded" if selections else "not_recorded"}
