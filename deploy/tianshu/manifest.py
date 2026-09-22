"""Release metadata and filesystem boundary checks; never opens application databases."""

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from jsonschema import Draft202012Validator

PRODUCTS = ("platform", "companion", "memory", "gateway")
HERE = Path(__file__).resolve().parent
EVIDENCE = {
    "linux_images",
    "four_service_tls",
    "release_acceptance",
    "log_recovery",
    "restore_drill",
}


class Refused(ValueError):
    """Static codes only: configuration/exception values must not reach output."""


def require(condition, code):
    if not condition:
        raise Refused(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def bad_constant(_):
        raise Refused("nonfinite_json")

    return json.loads(
        no_links(path).read_bytes(),
        object_pairs_hook=pairs,
        parse_constant=bad_constant,
    )


def write_json(path, document):
    Path(path).write_text(
        json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def relative(value):
    require(isinstance(value, str), "unsafe_relative_path")
    require(
        bool(re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value)),
        "unsafe_relative_path",
    )
    require(all(p not in {".", ".."} for p in value.split("/")), "unsafe_relative_path")
    # Windows normalizes trailing dots/spaces and recognizes device names even with extensions.
    require(
        all(
            not p.endswith(".")
            and p.split(".")[0].upper()
            not in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10)),
            }
            for p in value.split("/")
        ),
        "unsafe_relative_path",
    )
    return PurePosixPath(value)


def no_links(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        require(not part.is_symlink() and not part.is_junction(), "linked_path_refused")
    return path


def inside(root, name):
    relative(name)
    root = no_links(root).resolve()
    target = no_links(root.joinpath(*PurePosixPath(name).parts))
    require(target.resolve().is_relative_to(root), "path_escape")
    return target


def fresh_target(value):
    path = Path(value)
    require(path.is_absolute(), "absolute_target_required")
    no_links(path)
    require(path.parent.is_dir() and not path.exists(), "new_target_required")
    require(path.parent != path and len(path.parts) >= 3, "unsafe_target")
    return path


def load_manifest(path):
    document = read_json(path)
    schema = read_json(HERE / "release-manifest.schema.json")
    validator = Draft202012Validator(schema)
    require(not list(validator.iter_errors(document)), "manifest_schema_invalid")
    from observability_contract import validate

    validate(document)
    for field in ("services", "volumes", "contracts", "features"):
        values = [item["id"] for item in document[field]]
        require(len(values) == len(set(values)), "duplicate_manifest_id")
    services = {s["id"]: s for s in document["services"]}
    require(set(PRODUCTS) <= set(services), "core_services_missing")
    for product in PRODUCTS:
        require(services[product]["product"] == product, "service_product_mismatch")
    for service in services.values():
        relative(service["config_path"])
        require(service["hostname"] in service["tls_server_names"], "tls_name_missing")
    mounts = [v for v in document["volumes"] if v["mount"]]
    for volume in document["volumes"]:
        host = relative(volume["host_path"])
        require(volume["owner_service"] in services, "unknown_volume_owner")
        require(
            services[volume["owner_service"]]["product"] == volume["product"],
            "volume_product_mismatch",
        )
        require(
            ".." not in PurePosixPath(volume["container_path"]).parts,
            "unsafe_container_path",
        )
        if volume["mount"]:
            require(
                volume["kind"] == "directory"
                and volume["category"] in {"state", "logs", "observability_state"},
                "invalid_physical_mount",
            )
        else:
            parents = [
                m for m in mounts if host.is_relative_to(PurePosixPath(m["host_path"]))
            ]
            require(len(parents) == 1, "member_requires_one_mount")
            parent = parents[0]
            require(
                all(
                    volume[k] == parent[k]
                    for k in ("owner_service", "product", "backup_group")
                ),
                "inconsistent_backup_group",
            )
            suffix = host.relative_to(PurePosixPath(parent["host_path"]))
            require(
                str(PurePosixPath(parent["container_path"]) / suffix)
                == volume["container_path"],
                "member_container_mismatch",
            )
    for index, a in enumerate(mounts):
        for b in mounts[index + 1 :]:
            pa, pb = PurePosixPath(a["host_path"]), PurePosixPath(b["host_path"])
            require(
                not (pa.is_relative_to(pb) or pb.is_relative_to(pa)),
                "overlapping_mounts",
            )
            require(a["backup_group"] != b["backup_group"], "shared_backup_group")
    for contract in document["contracts"]:
        relative(contract["path"])
        require(
            contract["path"] == "contracts/" + contract["id"], "contract_path_mismatch"
        )
        names = [str(relative(f["path"])) for f in contract["files"]]
        require(
            len(names) == len(set(names)) and "manifest.json" in names,
            "contract_inventory_invalid",
        )
        require(
            next(f["sha256"] for f in contract["files"] if f["path"] == "manifest.json")
            == contract["manifest_sha256"],
            "contract_manifest_hash_mismatch",
        )
    if document["status"] == "verified":
        require(
            {e["kind"] for e in document["evidence"]} == EVIDENCE,
            "release_evidence_missing",
        )
        require(
            all(
                not f["enabled"] or f["verification"] == "verified"
                for f in document["features"]
            ),
            "enabled_feature_unverified",
        )
    return document


def check_contracts(manifest, root):
    """Inventory includes exact original bytes, including line endings and the manifest itself."""
    for contract in manifest["contracts"]:
        directory = inside(root, contract["id"])
        expected = {f["path"] for f in contract["files"]}
        require(directory.is_dir(), "contract_directory_missing")
        found = set()
        for file in directory.rglob("*"):
            no_links(file)
            if file.is_file():
                found.add(file.relative_to(directory).as_posix())
        require(found == expected, "contract_inventory_changed")
        for file in contract["files"]:
            require(
                digest(inside(directory, file["path"]).read_bytes()) == file["sha256"],
                "contract_bytes_changed",
            )


def check_evidence(manifest, root):
    require(manifest["status"] == "verified", "release_candidate")
    binding = {p: manifest["products"][p] for p in PRODUCTS}
    contracts = {c["id"]: c["manifest_sha256"] for c in manifest["contracts"]}
    for evidence in manifest["evidence"]:
        path = inside(root, evidence["path"])
        require(
            digest(path.read_bytes()) == evidence["sha256"], "evidence_hash_mismatch"
        )
        report = read_json(path)
        require(
            report.get("result") == "passed"
            and report.get("kind") == evidence["kind"]
            and report.get("release_id") == manifest["release_id"]
            and report.get("products") == binding
            and report.get("contracts") == contracts
            and report.get("synthetic_only") is False
            and report.get("skipped") == 0,
            "evidence_binding_mismatch",
        )
