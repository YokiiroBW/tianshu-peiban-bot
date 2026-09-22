"""Three-way reconciliation; no receipt here authorizes application segment deletion."""

import argparse
import json
from pathlib import Path

from policy import Policy, digest, segments
from query import LokiClient


def read_logs(roots, policy):
    records, invalid, partial = [], 0, 0
    for service, root in roots.items():
        for path in segments(root):
            with path.open("rb") as stream:
                while raw := stream.readline(4097):
                    if not raw.endswith(b"\n"):
                        # An active tail is unknown until the writer completes its LF.
                        if len(raw) <= 4096 and not stream.read(1):
                            partial += 1
                            break
                        invalid += 1
                        while raw and not raw.endswith(b"\n"):
                            raw = stream.readline(4097)
                        continue
                    try:
                        event = policy.line(raw)
                        if event["service"] not in (
                            {"memory", "memory-knowledge"}
                            if service == "memory"
                            else {service}
                        ):
                            raise ValueError()
                        records.append(event)
                    except ValueError:
                        invalid += 1
    return records, invalid, partial


def index(records):
    by_id, sequence, duplicates, conflicts = {}, {}, 0, 0
    for event in records:
        ident = event["event_id"]
        fingerprint = digest(event)
        if ident in by_id:
            duplicates += 1
            conflicts += by_id[ident] != fingerprint
        by_id[ident] = fingerprint
        key = (event["service"], event["instance_id"], event["sequence"])
        if key in sequence and sequence[key] != ident:
            conflicts += 1
        sequence[key] = ident
    groups = {}
    for service, instance, seq in sequence:
        groups.setdefault((service, instance), []).append(seq)
    gaps = sum(max(values) - len(values) for values in groups.values())
    return by_id, duplicates, conflicts, gaps


def compare(generated, landed, retrieved, invalid=0, partial=0):
    g, gd, gc, gg = index(generated)
    landed_ids, ld, lc, lg = index(landed)
    r, rd, rc, rg = index(retrieved)
    mismatches = sum(landed_ids[k] != r[k] for k in landed_ids.keys() & r.keys()) + sum(
        g[k] != landed_ids[k] for k in g.keys() & landed_ids.keys()
    )
    result = {
        "generated_unique": len(g),
        "landed_unique": len(landed_ids),
        "retrieved_unique": len(r),
        "missing_at_source": len(g.keys() - landed_ids.keys()),
        "missing_in_loki": len(landed_ids.keys() - r.keys()),
        "unexpected_at_source": len(landed_ids.keys() - g.keys()),
        "unexpected_in_loki": len(r.keys() - landed_ids.keys()),
        "source_duplicates": ld,
        "retrieval_duplicates": rd,
        "identity_conflicts": gc + lc + rc,
        "content_mismatches": mismatches,
        "sequence_gaps": gg + lg + rg,
        "invalid_source_lines": invalid,
        "partial_source_lines": partial,
        "application_reclamation_authorized": False,
    }
    result["status"] = (
        "passed"
        if not any(
            result[k]
            for k in (
                "missing_at_source",
                "missing_in_loki",
                "unexpected_at_source",
                "unexpected_in_loki",
                "identity_conflicts",
                "content_mismatches",
                "sequence_gaps",
                "invalid_source_lines",
                "partial_source_lines",
            )
        )
        else "failed"
    )
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--generated",
        type=Path,
        required=True,
        help="Synthetic fixture JSONL with complete instance ranges",
    )
    p.add_argument(
        "--roots",
        type=Path,
        required=True,
        help="Explicit product-to-log-directory JSON",
    )
    p.add_argument(
        "--snapshot", type=Path, default=Path(__file__).with_name("vocabulary.json")
    )
    p.add_argument("--loki-url", required=True)
    p.add_argument("--ca", type=Path, required=True)
    p.add_argument("--token-file", type=Path, required=True)
    p.add_argument(
        "--start-ns",
        type=int,
        required=True,
        help="Collector timestamp lower bound, not event timestamp",
    )
    p.add_argument("--end-ns", type=int, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    report = {"status": "error", "application_reclamation_authorized": False}
    try:
        policy = Policy(json.loads(args.snapshot.read_bytes()))
        generated = [
            policy.line(line)
            for line in args.generated.read_bytes().splitlines(keepends=True)
        ]
        landed, invalid, partial = read_logs(
            json.loads(args.roots.read_bytes()), policy
        )
        client = LokiClient(args.loki_url, args.ca, args.token_file.read_text().strip())
        # This selector is for a dedicated synthetic tenant/run; production monitor uses event IDs.
        rows = client.range('{stack="tianshu"}', args.start_ns, args.end_ns)
        retrieved = [policy.line(line.encode() + b"\n") for _, line in rows]
        report = compare(generated, landed, retrieved, invalid, partial)
    except Exception:
        report["error_code"] = "reconciliation_unverified"
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
