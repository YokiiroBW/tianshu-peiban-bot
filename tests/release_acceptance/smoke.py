"""Exercise the runner over real loopback TLS to synthetic doubles; not product acceptance."""

import argparse
import json
import tempfile
from pathlib import Path

from acceptance.evidence import Report
from acceptance.inputs import load_binding
from acceptance.observation import observe
from acceptance.suite import Suite
from fixtures import Harness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--contracts-root", required=True)
    parser.add_argument(
        "--mapping", default=str(Path(__file__).parent / "depa-mapping.json")
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    binding = load_binding(args.manifest, args.mapping, args.contracts_root)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    # Certificates/credentials are deleted; only redacted report artifacts survive.
    with (
        tempfile.TemporaryDirectory(dir=output) as temporary,
        Harness(temporary, binding, tls=True) as harness,
    ):
        report = Report("local", binding, "synthetic")
        Suite(harness.config, binding, report).execute()
        document = report.save(output / "suite")
        observation = Report("local", binding, "synthetic")
        observation.data["kind"] = "readiness_observation"
        observe(
            harness.config,
            observation,
            output / "short-observation",
            duration=0.2,
            interval=0.1,
        )
        observation.save(output / "short-observation")
    print(
        json.dumps(
            {
                "runtime_kind": "synthetic",
                "verdict": document["verdict"],
                "passed_cases": sum(x["status"] == "pass" for x in document["results"]),
            }
        )
    )
    return 1 if any(x["status"] == "fail" for x in document["results"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
