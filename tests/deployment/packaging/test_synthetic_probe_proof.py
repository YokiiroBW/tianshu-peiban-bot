"""Synthetic dialogue proof framing used by the isolated NAS acceptance hook."""

import hashlib
import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "deploy/tianshu/acceptance/nas-a3-dialogue5"))
from synthetic_probe_proof import InvalidProof, parse  # noqa: E402


PROOF = {
    "schema_version": "synthetic-dialogue-proof/v1",
    "observed_at": "2026-09-25T02:32:38Z",
    "client_id": "00000000-0000-4000-8000-000000000001",
    "message_id": "message:first",
    "turn_id": "turn:first",
    "reply_ids": ["reply:first"],
    "receipt_state": "accepted",
    "turn_phase": "sent",
    "reply_count": 1,
    "reply_state": "sent",
    "content_state": "available",
    "reply_sha256": hashlib.sha256(b"DEP-G synthetic recorded reply.").hexdigest(),
}


def framed(proof=PROOF, ending="\n"):
    return (json.dumps(proof, sort_keys=True) + ending + "container_probe_passed" + ending).encode()


class SyntheticProofTests(unittest.TestCase):
    def test_exact_success_and_legacy_sentinel(self):
        self.assertEqual(parse(framed()), PROOF)
        self.assertEqual(parse(framed(ending="\r\n")), PROOF)
        self.assertEqual(parse(framed() + b"\n"), PROOF)

    def test_missing_or_mixed_output_rejected(self):
        examples = (
            b"",
            b"container_probe_passed\n",
            b"container_probe_failed\n",
            framed() + b"unexpected\n",
            framed() + framed(),
            b"notice\n" + framed(),
            framed().replace(b"container_probe_passed", b"container_probe_failed"),
            b"\xff" + framed(),
            b"x" * 4097,
        )
        for value in examples:
            with self.subTest(value=value[:20]), self.assertRaises(InvalidProof):
                parse(value)

    def test_wrong_schema_or_unsent_result_rejected(self):
        for key, value in (
            ("schema_version", "unknown"),
            ("receipt_state", "rejected"),
            ("turn_phase", "failed"),
            ("reply_state", "pending"),
            ("content_state", "missing"),
            ("reply_count", 0),
            ("reply_ids", []),
            ("observed_at", "not-a-time"),
        ):
            candidate = dict(PROOF, **{key: value})
            with self.subTest(key=key), self.assertRaises(InvalidProof):
                parse(framed(candidate))
        with self.assertRaises(InvalidProof):
            parse(framed(dict(PROOF, unexpected="secret")))


if __name__ == "__main__":
    unittest.main()
