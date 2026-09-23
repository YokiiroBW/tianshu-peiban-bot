"""Validate explicit DEP-A inputs and create a new local bundle. Never starts containers."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl

import configs
from compose import compose
from policy import Policy

HERE = Path(__file__).resolve().parent
TLS_FILES = (
    "ca.pem",
    "client-ca.pem",
    "guard.pem",
    "guard.key",
    "loki.pem",
    "loki.key",
    "vector.pem",
    "vector.key",
    "grafana.pem",
    "grafana.key",
    "prometheus.pem",
    "prometheus.key",
    "client.pem",
    "client.key",
)
TOKEN_FILES = ("writer_token", "query_token", "metrics_token", "grafana_admin_password")


def confined(root, relative, exists=True):
    if (
        not isinstance(relative, str)
        or "\\" in relative
        or ":" in relative
        or "\x00" in relative
    ):
        raise ValueError("unsafe_relative_path")
    posix = PurePosixPath(relative)
    if (
        posix.is_absolute()
        or not posix.parts
        or any(part in (".", "..") for part in relative.split("/"))
    ):
        raise ValueError("unsafe_relative_path")
    path = root.joinpath(*posix.parts)
    if path.resolve().is_relative_to(root.resolve()) is False:
        raise ValueError("path_escape")
    for candidate in [path, *path.parents]:
        if candidate == root.parent:
            break
        if candidate.is_symlink() or (
            hasattr(candidate, "is_junction") and candidate.is_junction()
        ):
            raise ValueError("linked_path_refused")
    if exists and not path.exists():
        raise ValueError("required_path_missing")
    return path


def verify_tls(
    ca, certificate, key, hostname, client_ca=None, client_cert=None, client_key=None
):
    """Actual OpenSSL handshake via memory buffers: verify trust, expiry, SAN, key and EKU."""
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(str(certificate), str(key))
    if client_ca:
        server.load_verify_locations(cafile=str(client_ca))
        server.verify_mode = ssl.CERT_REQUIRED
    client = ssl.create_default_context(cafile=str(ca))
    if client_cert:
        client.load_cert_chain(str(client_cert), str(client_key))
    ci, co, si, so = [ssl.MemoryBIO() for _ in range(4)]
    c = client.wrap_bio(ci, co, server_hostname=hostname)
    s = server.wrap_bio(si, so, server_side=True)
    done = set()
    for _ in range(32):
        for label, connection in (("c", c), ("s", s)):
            try:
                connection.do_handshake()
                done.add(label)
            except ssl.SSLWantReadError:
                pass
        if co.pending:
            si.write(co.read())
        if so.pending:
            ci.write(so.read())
        if len(done) == 2:
            return
    raise ValueError("tls_handshake_incomplete")


def manifest_logs(manifest, snapshot, root):
    if manifest.get("schema_version") != "1.0.0":
        raise ValueError("unsupported_release_manifest")
    for service, product in snapshot["products"].items():
        source = manifest["products"][service]["source"]
        if source != {"repo": product["repo"], "commit": product["commit"]}:
            raise ValueError("vocabulary_source_commit_mismatch")
    matches = [c for c in manifest["contracts"] if c["id"] == "diagnostics/v1"]
    if len(matches) != 1:
        raise ValueError("diagnostics_contract_missing")
    contract = matches[0]
    if contract["manifest_sha256"] != snapshot["contract"]["manifest_sha256"]:
        raise ValueError("contract_manifest_mismatch")
    files = {f["path"]: f["sha256"] for f in contract["files"]}
    if files != snapshot["contract"]["files"]:
        raise ValueError("contract_files_mismatch")
    contract_path = confined(root, contract["path"])
    Policy(snapshot).verify_contract(contract_path)
    logs = {}
    for service in configs.PRODUCTS:
        candidates = [
            v
            for v in manifest["volumes"]
            if v["product"] == service and v["category"] == "logs" and v["mount"]
        ]
        if len(candidates) != 1 or candidates[0]["kind"] != "directory":
            raise ValueError("one_log_directory_per_product_required")
        logs[service] = confined(root, candidates[0]["host_path"])
    for a in logs.values():
        if not a.is_dir() or any(
            a != b and (a.is_relative_to(b) or b.is_relative_to(a))
            for b in logs.values()
        ):
            raise ValueError("log_roots_overlap")
    if len(set(logs.values())) != 4:
        raise ValueError("log_roots_not_exclusive")
    return logs, contract_path


def prepare(root, settings, manifest, output_relative, snapshot, candidate=False):
    root = root.resolve(strict=True)
    from nas_resources import bind

    profile = bind(root, settings.get("resource_profile"))
    from nas_resources import network_plan

    subnets = network_plan(root, settings.get("network_subnets"))
    if profile is not None and not candidate:
        raise ValueError("nas_qa_profile_not_release_approved")
    output = confined(root, output_relative, exists=False)
    if output.exists():
        raise ValueError("new_bundle_directory_required")
    logs, contract = manifest_logs(manifest, snapshot, root)
    tls = {name: confined(root, settings["tls"][name]) for name in TLS_FILES}
    tokens = {name: confined(root, settings["secrets"][name]) for name in TOKEN_FILES}
    if any(not p.is_file() for p in [*tls.values(), *tokens.values()]):
        raise ValueError("file_reference_required")
    for p in [*logs.values(), contract, *tls.values(), *tokens.values()]:
        if p.is_relative_to(output) or output.is_relative_to(p):
            raise ValueError("bundle_overlaps_input")
    values = [tokens[name].read_text().strip() for name in TOKEN_FILES]
    if len(set(values)) != 4 or any(
        not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token) for token in values
    ):
        raise ValueError("distinct_random_tokens_required")
    for role, hostname in (
        ("guard", "obs-guard"),
        ("loki", "obs-loki"),
        ("vector", "obs-vector"),
        ("prometheus", "obs-prometheus"),
        ("grafana", settings["grafana_hostname"]),
    ):
        args = (
            {}
            if role != "loki"
            else {
                "client_ca": tls["client-ca.pem"],
                "client_cert": tls["client.pem"],
                "client_key": tls["client.key"],
            }
        )
        verify_tls(
            tls["ca.pem"], tls[role + ".pem"], tls[role + ".key"], hostname, **args
        )
    budgets = settings["log_budgets"]
    if set(budgets) != set(configs.PRODUCTS) or any(
        type(n) is not int or not 32 * 1024**2 <= n <= 64 * 1024**3
        for n in budgets.values()
    ):
        raise ValueError("application_log_budgets_required")
    ports = settings["ports"]
    if (
        set(ports) != {"grafana", "query"}
        or len(set(ports.values())) != 2
        or any(type(n) is not int or not 1024 <= n <= 65535 for n in ports.values())
    ):
        raise ValueError("explicit_distinct_ports_required")
    versions = json.loads((HERE / "versions.json").read_bytes())
    images = {}
    for name, spec in versions["images"].items():
        override = settings.get("image_digests", {}).get(name)
        if override is None and not candidate:
            raise ValueError("verified_image_digests_required")
        if override is not None and not re.fullmatch(r"sha256:[a-f0-9]{64}", override):
            raise ValueError("invalid_image_digest")
        images[name] = spec["reference"] + ("@" + override if override else "")
    if not candidate:
        # No product reclaim protocol or joint Linux evidence is bundled. Cannot promote here.
        raise ValueError("daily_use_release_gate_unclosed")

    def write(name, obj):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

    output.mkdir(parents=True)
    for name in versions["images"]:
        (output / "data" / name).mkdir(parents=True)
    (output / "code").mkdir()
    for name in (
        "guard.py",
        "guard_lifecycle.py",
        "monitor.py",
        "policy.py",
        "query.py",
        "transport.py",
        "alerts.py",
        "grafana-entrypoint.sh",
    ):
        shutil.copyfile(HERE / name, output / "code" / name)
    write("code/vocabulary.json", snapshot)
    stack = compose(output, logs, contract, tls, tokens, images, ports, profile)
    if subnets:
        for name, subnet in subnets.items():
            stack["networks"][name]["ipam"] = {"config": [{"subnet": subnet}]}
    stack["services"]["obs-grafana"]["environment"].update(
        {
            "GF_SERVER_DOMAIN": settings["grafana_hostname"],
            "GF_SERVER_ROOT_URL": f"https://{settings['grafana_hostname']}:{ports['grafana']}/",
            "GF_PLUGINS_PREINSTALL_DISABLED": "true",
        }
    )
    write("compose.yaml", stack)
    write("config/vector.json", configs.vector(snapshot))
    write("config/loki.json", configs.loki())
    write("config/prometheus.json", configs.prometheus())
    write(
        "config/prometheus-web.json",
        {
            "tls_server_config": {
                "cert_file": "/run/tls/prometheus.pem",
                "key_file": "/run/tls/prometheus.key",
            }
        },
    )
    write(
        "config/guard.json",
        {
            "loki_url": "https://obs-loki:3100",
            "ca": "/run/tls/ca.pem",
            "client_cert": "/run/tls/client.pem",
            "client_key": "/run/tls/client.key",
            "server_cert": "/run/tls/guard.pem",
            "server_key": "/run/tls/guard.key",
            "ledger": "/var/lib/guard/integrity.sqlite",
            "snapshot": "/opt/observability/vocabulary.json",
            "contract": "/contracts/diagnostics/v1",
            "tokens": {
                role: "/run/secrets/" + role + "_token"
                for role in ("writer", "query", "metrics")
            },
            "log_roots": {role: "/sources/" + role for role in configs.PRODUCTS},
            "log_budgets": budgets,
            "storage_roots": {
                "vector": "/capacity/vector",
                "loki": "/capacity/loki",
                "guard": "/var/lib/guard",
            },
            "reserve_bytes": 1024**3,
            "interval_seconds": 30,
            "ledger_max_events": 1_000_000,
        },
    )
    write(
        "config/provisioning/datasources/tianshu.yaml",
        configs.datasources(tls["ca.pem"].read_text()),
    )
    write("config/provisioning/alerting/tianshu.yaml", configs.alert_rules())
    write(
        "config/provisioning/dashboards/tianshu.yaml",
        {
            "apiVersion": 1,
            "providers": [
                {
                    "name": "TianShu",
                    "folder": "TianShu",
                    "type": "file",
                    "disableDeletion": True,
                    "options": {"path": "/etc/grafana/dashboards"},
                }
            ],
        },
    )
    write("config/dashboards/tianshu.json", configs.dashboard())
    write(
        "binding.json",
        {
            "release_id": manifest["release_id"],
            "manifest_sha256": hashlib.sha256(
                json.dumps(manifest, sort_keys=True).encode()
            ).hexdigest(),
            "binding_hash_basis": "semantic_sorted_json",
            "source_commits": {p: v["commit"] for p, v in snapshot["products"].items()},
            "images": images,
            "status": "candidate",
            "blockers": [
                "linux_stack_unverified",
                "application_reclamation_missing",
                "alert_delivery_unconfigured",
                "capacity_30_days_unverified",
            ],
        },
    )
    return output


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--deployment-root", type=Path, required=True)
    p.add_argument("--release-manifest", type=Path, required=True)
    p.add_argument("--settings", type=Path, required=True)
    p.add_argument("--output-relative", required=True)
    p.add_argument("--snapshot", type=Path, default=HERE / "vocabulary.json")
    p.add_argument(
        "--candidate",
        action="store_true",
        help="Explicit candidate; never marks Linux or daily-use accepted",
    )
    args = p.parse_args()
    try:
        prepare(
            args.deployment_root,
            json.loads(args.settings.read_bytes()),
            json.loads(args.release_manifest.read_bytes()),
            args.output_relative,
            json.loads(args.snapshot.read_bytes()),
            args.candidate,
        )
    except Exception as exc:
        allowed = (
            str(exc)
            if type(exc) is ValueError and re.fullmatch(r"[a-z_]+", str(exc))
            else "configuration_rejected"
        )
        print(json.dumps({"status": "rejected", "error_code": allowed}))
        return 1
    print(json.dumps({"status": "candidate_generated", "started_services": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
