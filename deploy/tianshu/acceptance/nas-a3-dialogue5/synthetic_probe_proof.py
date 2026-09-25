"""Parse the synthetic probe's versioned proof and legacy CLI success sentinel."""

import json
import re
from datetime import datetime


FIELDS = frozenset({
    "schema_version", "observed_at", "client_id", "message_id", "turn_id",
    "reply_ids", "receipt_state", "turn_phase", "reply_count",
    "reply_state", "content_state", "reply_sha256",
})
SENTINEL = "container_probe_passed"
MAX_BYTES = 4096


class InvalidProof(ValueError):
    pass


def parse(stdout):
    if not isinstance(stdout, bytes) or not stdout or len(stdout) > MAX_BYTES:
        raise InvalidProof("standard_probe_output_size_invalid")
    try:
        lines = stdout.decode("utf-8", "strict").rstrip("\r\n").replace("\r\n", "\n").split("\n")
    except UnicodeDecodeError:
        raise InvalidProof("standard_probe_output_encoding_invalid") from None
    if len(lines) != 2 or not lines[0] or lines[1] != SENTINEL or "\r" in lines[0]:
        raise InvalidProof("standard_probe_output_envelope_invalid")
    try:
        proof = json.loads(lines[0])
    except ValueError:
        raise InvalidProof("standard_probe_json_invalid") from None
    if not isinstance(proof, dict) or set(proof) != FIELDS:
        raise InvalidProof("standard_probe_fields_invalid")
    if proof["schema_version"] != "synthetic-dialogue-proof/v1":
        raise InvalidProof("standard_probe_schema_invalid")
    if (
        proof["receipt_state"] != "accepted"
        or proof["turn_phase"] != "sent"
        or proof["reply_state"] != "sent"
        or proof["content_state"] != "available"
    ):
        raise InvalidProof("standard_probe_delivery_incomplete")
    if not all(isinstance(proof[key], str) and proof[key] for key in ("client_id", "message_id", "turn_id")):
        raise InvalidProof("standard_probe_identity_invalid")
    reply_ids = proof["reply_ids"]
    if (
        not isinstance(reply_ids, list)
        or not reply_ids
        or any(not isinstance(value, str) or not value for value in reply_ids)
        or len(reply_ids) != len(set(reply_ids))
        or type(proof["reply_count"]) is not int
        or proof["reply_count"] != len(reply_ids)
    ):
        raise InvalidProof("standard_probe_replies_invalid")
    if not isinstance(proof["reply_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", proof["reply_sha256"]):
        raise InvalidProof("standard_probe_reply_hash_invalid")
    try:
        observed = datetime.fromisoformat(proof["observed_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise InvalidProof("standard_probe_observed_time_invalid") from None
    if observed.tzinfo is None:
        raise InvalidProof("standard_probe_observed_time_invalid")
    return proof
