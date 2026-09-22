"""Release 1.1 storage contract; the four-product 1.0 shape remains readable."""

from manifest import PRODUCTS, require

COMPONENTS = ("vector", "loki", "grafana", "prometheus", "guard")


def validate(manifest):
    extra_services = [s for s in manifest["services"] if s["product"] not in PRODUCTS]
    extra_volumes = [v for v in manifest["volumes"] if v["product"] not in PRODUCTS]
    if manifest["schema_version"] == "1.0.0":
        require(
            not extra_services
            and not extra_volumes
            and "observability" not in manifest,
            "legacy_observability_extension_refused",
        )
        return
    obs = manifest["observability"]
    require(obs["output_relative"] == "observability", "observability_layout_mismatch")
    require(
        {s["id"] for s in extra_services} == {"obs-" + c for c in COMPONENTS},
        "observability_services_missing",
    )
    require(len(extra_volumes) == len(COMPONENTS), "observability_volumes_missing")
    for name in COMPONENTS:
        expected = {
            "id": "obs-" + name + "-state",
            "product": "observability",
            "category": "observability_state",
            "host_path": "observability/data/" + name,
            "container_path": "/var/lib/" + name,
            "owner_service": "obs-" + name,
            "backup_group": "obs-" + name,
            "mount": True,
            "kind": "directory",
        }
        require(expected in extra_volumes, "observability_volume_layout_mismatch")


def legacy_view(manifest):
    """Explicit configure.py compatibility input, NOT an alternative release authority."""
    validate(manifest)
    return {
        **{k: v for k, v in manifest.items() if k != "observability"},
        "schema_version": "1.0.0",
        "services": [s for s in manifest["services"] if s["product"] in PRODUCTS],
        "volumes": [v for v in manifest["volumes"] if v["product"] in PRODUCTS],
    }
