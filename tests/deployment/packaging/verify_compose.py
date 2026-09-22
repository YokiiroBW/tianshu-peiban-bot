"""Validate a synthetic initialized bundle with a real, explicit Compose executable.

Only `version` and `config` are invoked; no daemon, build, pull, or container operation.
Resolved environment values stay in memory and are never included in the report.
"""

import argparse
import json
import subprocess

from test_packaging import PackagingTests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", required=True)
    args = parser.parse_args()
    case = PackagingTests()
    case.setUp()
    try:
        case.env["TS_CORE_GATEWAY"] += "$DEP_A_UNDEFINED_CANARY"
        case.init()
        version = (
            subprocess.run(
                [args.compose, "version", "--short"],
                capture_output=True,
                check=True,
                timeout=15,
            )
            .stdout.decode()
            .strip()
        )
        completed = subprocess.run(
            [
                args.compose,
                "--project-directory",
                str(case.output),
                "-f",
                str(case.output / "compose.json"),
                "config",
                "--format",
                "json",
            ],
            capture_output=True,
            timeout=30,
        )
        facts = {
            "exit_code": completed.returncode,
            "version": version,
            "daemon_used": False,
            "containers_started": False,
        }
        if completed.returncode == 0:
            document = json.loads(completed.stdout)
            services = document["services"]
            facts["four_services"] = set(services) == {
                "platform",
                "companion",
                "memory",
                "gateway",
            }
            facts["one_public_port"] = (
                sum(len(s.get("ports", [])) for s in services.values()) == 1
            )
            facts["no_socket_mount"] = all(
                "docker.sock" not in str(v)
                for s in services.values()
                for v in s["volumes"]
            )
            facts["non_root"] = all(
                s["user"] == "10001:10001" for s in services.values()
            )
            # config output escapes '$' for a subsequent Compose parse. It must retain the
            # canary text, not substitute host environment or remove it.
            value = services["gateway"]["environment"]["TS_CORE_GATEWAY"]
            facts["literal_dollar_preserved"] = (
                value.replace("$$", "$") == case.env["TS_CORE_GATEWAY"]
            )
        result = completed.returncode == 0 and all(
            v is True
            for k, v in facts.items()
            if k
            in {
                "four_services",
                "one_public_port",
                "no_socket_mount",
                "non_root",
                "literal_dollar_preserved",
            }
        )
        print(
            json.dumps(
                {
                    "kind": "compose_config_only",
                    "result": "passed" if result else "failed",
                    "facts": facts,
                },
                sort_keys=True,
            )
        )
        return 0 if result else 1
    finally:
        case.tearDown()


if __name__ == "__main__":
    raise SystemExit(main())
