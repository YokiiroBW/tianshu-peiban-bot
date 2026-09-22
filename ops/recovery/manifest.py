"""DEP-A release consumer and a private, explicit recovery file inventory."""

import re

from .safety import child, digest, read_bytes, read_json, relative, require

PRODUCTS = {"platform", "companion", "memory", "gateway"}
OBSERVABILITY = {"vector", "loki", "grafana", "prometheus", "guard"}
HEX = re.compile(r"[0-9a-f]{64}")
ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")


def identifier(value):
    require(isinstance(value, str) and ID.fullmatch(value), "invalid_identifier")
    return value


def sha256(value):
    require(isinstance(value, str) and HEX.fullmatch(value), "invalid_sha256")
    return value


def fields(value, names):
    require(
        isinstance(value, dict) and set(value) == set(names.split()),
        "unexpected_or_missing_fields",
    )


def release(raw):
    require(isinstance(raw, dict), "invalid_release_document")
    extended = raw.get("schema_version") == "1.1.0"
    fields(
        raw,
        "schema_version release_id status products contracts features volumes services blockers evidence"
        + (" observability" if extended else ""),
    )
    require(
        isinstance(raw, dict) and raw.get("schema_version") in {"1.0.0", "1.1.0"},
        "unsupported_release_schema",
    )
    identifier(raw["release_id"])
    require(raw.get("status") in {"candidate", "verified"}, "invalid_release_status")
    require(set(raw["products"]) == PRODUCTS, "four_products_required")
    allowed_products = PRODUCTS | ({"observability"} if extended else set())
    if extended:
        info = raw["observability"]
        fields(info, "source package_path output_relative compose_project_suffix")
        fields(info["source"], "repo commit")
        identifier(info["source"]["repo"])
        require(
            re.fullmatch(r"[0-9a-f]{40}", info["source"]["commit"]), "unpinned_source"
        )
        require(
            info["package_path"] == "deploy/observability"
            and info["output_relative"] == "observability"
            and info["compose_project_suffix"] == "-obs",
            "unsupported_observability_binding",
        )
    for product in raw["products"].values():
        fields(product, "source image service_role")
        source = product["source"]
        fields(source, "repo commit")
        require(
            isinstance(source["repo"], str) and source["repo"], "missing_repository"
        )
        require(
            re.fullmatch(r"[0-9a-f]{40}", source["commit"]) is not None,
            "unpinned_source",
        )
        image = product["image"]
        fields(image, "reference digest verification")
        require(
            isinstance(image["reference"], str) and image["reference"],
            "missing_image_reference",
        )
        require(
            image["verification"] in {"verified", "unverified"},
            "invalid_image_verification",
        )
        if image["digest"] is not None:
            require(image["digest"].startswith("sha256:"), "invalid_image_digest")
            sha256(image["digest"][7:])
        require(
            image["verification"] != "verified" or image["digest"] is not None,
            "unverified_image",
        )
    volumes = {}
    for volume in raw["volumes"]:
        fields(
            volume,
            "id product category host_path container_path owner_service backup_group mount kind",
        )
        key = identifier(volume["id"])
        require(key not in volumes, "duplicate_volume")
        require(volume["product"] in allowed_products, "unknown_product")
        require(
            volume["category"]
            in {"state", "logs", "guard", "sidecar"}
            | ({"observability_state"} if extended else set()),
            "invalid_volume_role",
        )
        relative(volume["host_path"])
        container = volume["container_path"]
        require(
            isinstance(container, str) and container.startswith("/"),
            "invalid_container_path",
        )
        relative(container[1:])
        require(
            type(volume["mount"]) is bool and volume["kind"] in {"directory", "file"},
            "invalid_volume_mount",
        )
        require(
            volume["owner_service"] and volume["backup_group"], "missing_volume_owner"
        )
        volumes[key] = volume
        if (
            volume["product"] == "observability"
            or volume["category"] == "observability_state"
        ):
            component = key.removeprefix("obs-").removesuffix("-state")
            require(
                component in OBSERVABILITY
                and volume
                == {
                    "id": "obs-" + component + "-state",
                    "product": "observability",
                    "category": "observability_state",
                    "host_path": "observability/data/" + component,
                    "container_path": "/var/lib/" + component,
                    "owner_service": "obs-" + component,
                    "backup_group": "obs-" + component,
                    "mount": True,
                    "kind": "directory",
                },
                "invalid_observability_volume",
            )
    if extended:
        require(
            {v["id"] for v in volumes.values() if v["product"] == "observability"}
            == {"obs-" + c + "-state" for c in OBSERVABILITY},
            "missing_observability_volume",
        )
    require(
        {v["product"] for v in volumes.values() if v["category"] == "state"}
        == PRODUCTS,
        "missing_product_state",
    )
    require(
        {v["product"] for v in volumes.values() if v["category"] == "logs"} == PRODUCTS,
        "missing_product_logs",
    )
    # Nested state/guard/sidecar views are legal; unrelated owners cannot share paths.
    for left in volumes.values():
        for right in volumes.values():
            a, b = left["host_path"].casefold(), right["host_path"].casefold()
            if a == b or a.startswith(b + "/"):
                require(
                    left["product"] == right["product"]
                    and left["backup_group"] == right["backup_group"],
                    "cross_product_volume_overlap",
                )
    services = raw["services"]
    for service in services:
        fields(
            service,
            "id product role port replicas hostname tls_server_names config_path",
        )
        relative(service["config_path"])
    require(
        {s["product"] for s in services} == allowed_products, "missing_product_service"
    )
    if extended:
        require(
            {s["id"] for s in services if s["product"] == "observability"}
            == {"obs-" + c for c in OBSERVABILITY},
            "missing_observability_service",
        )
    require(all(s["replicas"] == 1 for s in services), "single_owner_required")
    require(len({s["id"] for s in services}) == len(services), "duplicate_service")
    for volume in volumes.values():
        require(
            any(
                s["id"] == volume["owner_service"] and s["product"] == volume["product"]
                for s in services
            ),
            "invalid_volume_owner",
        )
    contract_paths = set()
    for contract in raw["contracts"]:
        fields(contract, "id path manifest_sha256 files")
        relative(contract["id"])
        base = relative(contract["path"])
        require(base not in contract_paths, "duplicate_contract")
        contract_paths.add(base)
        sha256(contract["manifest_sha256"])
        names = set()
        for file in contract["files"]:
            fields(file, "path sha256")
            name = relative(file["path"])
            require(name not in names, "duplicate_contract_file")
            names.add(name)
            sha256(file["sha256"])
    require(raw["contracts"], "contracts_required")
    require(
        isinstance(raw["features"], list) and isinstance(raw["blockers"], list),
        "feature_status_required",
    )
    for item in raw["features"]:
        fields(item, "id enabled verification reason")
    for item in raw["evidence"]:
        fields(item, "kind path sha256 result")
        relative(item["path"])
        sha256(item["sha256"])
        require(item["result"] == "passed", "invalid_evidence_result")
    # A deployment backup can carry evidence bytes, but this tool cannot infer their truth.
    # Verified-release acceptance needs the joint runner's yet-to-be-frozen evidence contract.
    require(raw["status"] != "verified", "verified_release_evidence_adapter_required")
    return raw


def load_deployment(root):
    raw_bytes = read_bytes(child(root, "release-manifest.json"), limit=4 * 1024 * 1024)
    document = release(read_json(child(root, "release-manifest.json")))
    inventory = read_json(child(root, "recovery-inventory.json"))
    fields(
        inventory,
        "schema_version release_manifest_sha256 resources guard_checks config_references",
    )
    require(inventory["schema_version"] == "1.0.0", "unsupported_inventory_schema")
    require(
        inventory["release_manifest_sha256"] == digest(raw_bytes),
        "release_binding_mismatch",
    )
    volumes = {v["id"]: v for v in document["volumes"]}
    resources = {}
    paths = set()
    for item in inventory["resources"]:
        fields(item, "id volume_id path kind")
        key = identifier(item["id"])
        require(key not in resources, "duplicate_resource")
        require(item["volume_id"] in volumes, "unknown_volume")
        volume = volumes[item["volume_id"]]
        path = relative(item["path"])
        base = volume["host_path"]
        require(path == base or path.startswith(base + "/"), "resource_outside_volume")
        require(path.casefold() not in paths, "duplicate_resource_path")
        paths.add(path.casefold())
        require(
            item["kind"] in {"sqlite", "guard", "file", "owner_lock"},
            "invalid_resource_kind",
        )
        require(
            volume["category"] not in {"logs", "observability_state"},
            "logs_enumerated_automatically",
        )
        require(
            item["kind"] != "guard" or volume["category"] == "guard",
            "guard_role_required",
        )
        resources[key] = {**item, "product": volume["product"]}
    require(
        {r["product"] for r in resources.values() if r["kind"] == "sqlite"} == PRODUCTS,
        "missing_product_database",
    )
    for item in resources.values():
        if item["kind"] == "owner_lock":
            require(
                item["product"] == "companion"
                and any(
                    r["product"] == "companion"
                    and r["kind"] == "sqlite"
                    and r["path"] + ".owner" == item["path"]
                    for r in resources.values()
                ),
                "invalid_owner_lock_resource",
            )
    require(
        any(
            r["product"] == "memory" and r["kind"] == "guard"
            for r in resources.values()
        ),
        "memory_guard_required",
    )
    require(
        any(c["kind"] == "memory-source-v3" for c in inventory["guard_checks"]),
        "memory_guard_check_required",
    )
    for check in inventory["guard_checks"]:
        fields(check, "kind database guard")
        require(check["kind"] == "memory-source-v3", "unsupported_guard_check")
        require(
            resources[check["database"]]["kind"] == "sqlite"
            and resources[check["guard"]]["kind"] == "guard",
            "invalid_guard_binding",
        )
        require(
            resources[check["database"]]["product"] == "memory"
            and resources[check["guard"]]["product"] == "memory",
            "invalid_guard_binding",
        )
    for ref in inventory["config_references"]:
        require(set(ref) == {"id", "reference", "sha256"}, "config_values_forbidden")
        identifier(ref["id"])
        require(
            re.fullmatch(r"[a-z][a-z0-9_-]*:[A-Za-z0-9_./-]+", ref["reference"])
            is not None,
            "invalid_config_reference",
        )
        sha256(ref["sha256"])
    require(inventory["config_references"], "config_references_required")
    return document, inventory, resources
