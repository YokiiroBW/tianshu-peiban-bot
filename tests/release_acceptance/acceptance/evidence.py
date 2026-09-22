"""Small, content-addressed reports. Responses and credentials never enter reports."""

import hashlib
import json
import platform
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("non_finite_json")

    return json.loads(
        Path(path).read_text("utf-8-sig"),
        object_pairs_hook=unique,
        parse_constant=invalid,
    )


class Report:
    def __init__(self, mode, binding, runtime_kind):
        self.started = time.monotonic()
        package = Path(__file__).parent
        implementation = {
            p.name: digest(p.read_bytes()) for p in sorted(package.glob("*.py"))
        }
        implementation["../run.py"] = digest((package.parent / "run.py").read_bytes())
        self.data = {
            "report_version": "dep-d/1",
            "kind": "release_acceptance",
            "run_id": str(uuid.uuid4()),
            "started_at": utc(),
            "mode": mode,
            "runtime_kind": runtime_kind,
            "python": platform.python_version(),
            "host_os": platform.system(),
            "implementation_sha256": digest(canonical(implementation)),
            "binding": binding,
            "results": [],
            "claims": {
                "real_model_quality": "not_run",
                "browser_rendering": "not_run",
                "nas_acceptance": "not_run",
                "observation_24h": "not_run",
            },
        }

    def add(self, case, status, code, facts=None, elapsed=0):
        self.data["results"].append(
            {
                "case": case,
                "status": status,
                "code": code,
                "duration_seconds": round(elapsed, 3),
                "facts": facts or {},
            }
        )

    def save(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.data["finished_at"] = utc()
        self.data["duration_seconds"] = round(time.monotonic() - self.started, 3)
        statuses = [x["status"] for x in self.data["results"]]
        self.data["verdict"] = (
            "failed"
            if "fail" in statuses
            else "incomplete"
            if any(x != "pass" for x in statuses) or not statuses
            else "synthetic_only"
            if self.data["runtime_kind"] == "synthetic"
            else "passed_with_limits"
        )
        # A digest is tamper evidence, not a signature or an independent attestation.
        document = dict(self.data, content_sha256=digest(canonical(self.data)))
        raw = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        self._atomic(directory / "report.json", raw)
        rows = [
            "# DEP-D release acceptance",
            "",
            f"Verdict: **{document['verdict']}**",
            f"Mode: {document['mode']}; runtime: {document['runtime_kind']}",
            f"Elapsed: {document['duration_seconds']} seconds",
            "",
            "| Case | Status | Reason |",
            "|---|---|---|",
        ]
        rows += [
            f"| {x['case']} | {x['status']} | {x['code']} |"
            for x in document["results"]
        ]
        rows += [
            "",
            "Candidate acceptance is not durable memory or archive completion.",
            "Recorded models do not establish model quality. HTTP cookie checks do not establish browser rendering.",
            "Local runs do not establish Linux container or NAS acceptance.",
            "Evidence hash: " + document["content_sha256"],
            "",
        ]
        self._atomic(directory / "summary.md", "\n".join(rows))
        return document

    @staticmethod
    def _atomic(path, text):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, "utf-8", newline="\n")
        temporary.replace(path)


def verify_report(path):
    document = read_json(path)
    expected = document.pop("content_sha256")
    return expected == digest(canonical(document))
