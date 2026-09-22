"""Explicit four-product configuration binding. Does not mint origins or model publications."""

import hashlib
import ipaddress
import os
import re
import secrets
import ssl
from datetime import datetime, timezone
from urllib.parse import urlsplit

from cryptography import x509

from manifest import PRODUCTS, inside, read_json, require

PORTS = {"platform": 8443, "companion": 8765, "memory": 8130, "gateway": 8443}
STATE = {
    "platform": "/var/lib/tianshu",
    "companion": "/data",
    "memory": "/srv/tianshu",
    "gateway": "/var/lib/tianshu",
}
DATABASE = {
    "platform": "platform.sqlite",
    "companion": "companion.db",
    "memory": "memory.sqlite",
    "gateway": "diagnostics.sqlite",
}
CA = "/etc/tianshu/tls/ca.pem"
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{2,95}\Z")
ENV_KEYS = {
    "token_env",
    "credential_env",
    "admin_token_env",
    "platform_origin_env",
    "ready_token_env",
    "probe_token_env",
}


def endpoint(product):
    return f"https://{product}.internal:{PORTS[product]}"


def shape(document, fields, code):
    require(isinstance(document, dict) and set(document) == set(fields), code)


def load_inputs(path):
    site = read_json(path)
    from resource_profile import validate

    require(isinstance(site, dict), "inputs_shape_invalid")
    profile = validate(site.get("resource_profile"))
    shape(
        {k: v for k, v in site.items() if k != "resource_profile"},
        {
            "project_name",
            "web_origin",
            "bind_address",
            "web_port",
            "subnet",
            "service_ips",
            "config_files",
            "tls",
            "diagnostics_env",
        },
        "inputs_shape_invalid",
    )
    if profile is not None:
        require(
            isinstance(site["project_name"], str)
            and site["project_name"].startswith("tianshu-qa-"),
            "nas_profile_synthetic_only",
        )
        require(site["bind_address"] == "127.0.0.1", "nas_profile_loopback_only")
        require(
            isinstance(site["tls"], dict)
            and all(
                isinstance(v, dict) and v.get("provenance") == "isolated_test"
                for v in site["tls"].values()
            ),
            "nas_profile_test_tls_only",
        )
    require(
        bool(re.fullmatch(r"tianshu-[a-z0-9-]{3,40}", site["project_name"])),
        "dedicated_project_required",
    )
    require(
        site["project_name"] not in {"tianshu-control-hub", "tianshu-observability"},
        "reserved_project",
    )
    ip = ipaddress.ip_address(site["bind_address"])
    require(
        ip.version == 4 and not ip.is_unspecified and not ip.is_multicast,
        "explicit_ipv4_bind_required",
    )
    require(
        type(site["web_port"]) is int and 1024 <= site["web_port"] <= 65535,
        "invalid_public_port",
    )
    origin = urlsplit(site["web_origin"])
    require(
        origin.scheme == "https"
        and origin.hostname
        and not origin.username
        and not origin.password
        and not origin.path
        and not origin.query
        and not origin.fragment
        and (origin.port or 443) == site["web_port"],
        "origin_port_mismatch",
    )
    require(not origin.hostname.endswith(".invalid"), "placeholder_origin")
    network = ipaddress.ip_network(site["subnet"], strict=True)
    require(
        network.version == 4 and 24 <= network.prefixlen <= 28 and network.is_private,
        "private_small_subnet_required",
    )
    for key in ("service_ips", "config_files", "tls", "diagnostics_env"):
        shape(site[key], PRODUCTS, "four_product_inputs_required")
    addresses = [ipaddress.ip_address(site["service_ips"][p]) for p in PRODUCTS]
    require(
        len(set(addresses)) == 4
        and all(
            a in network
            and a
            not in {
                network.network_address,
                network.broadcast_address,
                network.network_address + 1,
            }
            for a in addresses
        ),
        "invalid_service_ips",
    )
    require(
        len(set(site["diagnostics_env"].values())) == 4,
        "diagnostics_credentials_shared",
    )
    for value in site["diagnostics_env"].values():
        require(
            isinstance(value, str) and bool(ENV_NAME.fullmatch(value)),
            "invalid_env_reference",
        )
    return site


def tls_check(certificate, key, ca, names):
    """Real OpenSSL chain, expiry and hostname verification over memory BIOs, with no sockets."""
    leaf = x509.load_pem_x509_certificate(certificate.read_bytes())
    require(
        leaf.not_valid_before_utc
        <= datetime.now(timezone.utc)
        < leaf.not_valid_after_utc,
        "tls_leaf_expired_or_not_yet_valid",
    )
    require(leaf.issuer != leaf.subject, "self_signed_leaf_refused")
    try:
        require(
            not leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca,
            "tls_leaf_is_ca",
        )
    except x509.ExtensionNotFound:
        pass
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = ssl.TLSVersion.TLSv1_2
    server.load_cert_chain(certificate, key, password=lambda: "")
    client = ssl.create_default_context(cafile=str(ca))
    client.minimum_version = ssl.TLSVersion.TLSv1_2
    client.hostname_checks_common_name = False
    for name in names:
        cin, cout, sin, sout = (
            ssl.MemoryBIO(),
            ssl.MemoryBIO(),
            ssl.MemoryBIO(),
            ssl.MemoryBIO(),
        )
        c = client.wrap_bio(cin, cout, server_side=False, server_hostname=name)
        s = server.wrap_bio(sin, sout, server_side=True)
        done = [False, False]
        for _ in range(20):
            for index, peer in enumerate((c, s)):
                if not done[index]:
                    try:
                        peer.do_handshake()
                        done[index] = True
                    except ssl.SSLWantReadError:
                        pass
            if cout.pending:
                sin.write(cout.read())
            if sout.pending:
                cin.write(sout.read())
            if all(done):
                break
        require(all(done), "tls_handshake_failed")


def references(document):
    found = set()

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ENV_KEYS or key in {"$env", "$password_env"}:
                    require(
                        isinstance(child, str) and bool(ENV_NAME.fullmatch(child)),
                        "invalid_env_reference",
                    )
                    found.add(child)
                elif key == "secret_references":
                    require(isinstance(child, dict), "secret_references_invalid")
                    for name in child.values():
                        require(
                            isinstance(name, str) and bool(ENV_NAME.fullmatch(name)),
                            "invalid_env_reference",
                        )
                        found.add(name)
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(document)
    found.add("TIANSHU_DIAGNOSTICS_TOKEN")
    return found


def resolve_config(document, values):
    if isinstance(document, list):
        return [resolve_config(v, values) for v in document]
    if not isinstance(document, dict):
        return document
    if set(document) == {"$env"}:
        return values[document["$env"]]
    if set(document) == {"$password_env"}:
        password = values[document["$password_env"]]
        require(12 <= len(password) <= 256, "admin_password_length")
        salt = secrets.token_bytes(16)
        key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
        return "scrypt-v1$" + salt.hex() + "$" + key.hex()
    return {k: resolve_config(v, values) for k, v in document.items()}


def validate_configs(configs, site, manifest):
    p, c, m, g = [configs[k] for k in PRODUCTS]
    services = {s["id"]: s for s in manifest["services"] if s["product"] in PRODUCTS}
    require(set(services) == set(PRODUCTS), "unsupported_service_adapter")
    for name in PRODUCTS:
        s = services[name]
        require(
            s["port"] == PORTS[name]
            and s["hostname"] == name + ".internal"
            and s["config_path"] == f"config/{name}/settings.json",
            "service_mapping_changed",
        )
        mounts = [
            v for v in manifest["volumes"] if v["mount"] and v["owner_service"] == name
        ]
        require(
            {(v["category"], v["container_path"], v["host_path"]) for v in mounts}
            == {
                ("state", STATE[name], f"data/{name}"),
                ("logs", "/var/log/tianshu", f"logs/{name}"),
            },
            "volume_mapping_changed",
        )
    require(
        p["mode"] == "service_https" and p["storage"] == "sqlite_local",
        "platform_tls_mode",
    )
    require(
        m["mode"] == "source_sync" and "origins" not in m, "memory_real_source_required"
    )
    require(
        p["database_path"] == STATE["platform"] + "/" + DATABASE["platform"]
        and c["database_path"] == STATE["companion"] + "/" + DATABASE["companion"]
        and m["database_path"] == STATE["memory"] + "/" + DATABASE["memory"]
        and g["diagnostics_path"] == STATE["gateway"] + "/" + DATABASE["gateway"],
        "database_mapping_changed",
    )
    require(
        p["contract_directory"]
        == c["contracts_path"]
        == m["contract_directory"]
        == "/contracts/text-dialogue/v1"
        and g["contract_directory"] == "/contracts/text-dialogue/v1",
        "contract_mapping_changed",
    )
    require(
        p["diagnostics"]["contract_directory"]
        == g["diagnostics_contract_directory"]
        == "/contracts/diagnostics/v1",
        "diagnostics_mapping_changed",
    )
    require(
        p["diagnostics"]["log_directory"]
        == m["log_directory"]
        == g["observability"]["log_directory"]
        == "/var/log/tianshu",
        "durable_logs_required",
    )
    for budget in (
        p["diagnostics"].get("log_directory_bytes", 1073741824),
        g["observability"].get("max_directory_bytes", 1073741824),
    ):
        require(
            type(budget) is int and 33554432 <= budget <= 68719476736,
            "invalid_log_directory_budget",
        )
    require(
        "log_directory_bytes" not in m, "memory_log_budget_not_configurable_in_baseline"
    )
    require(
        p["diagnostics"]["ready_token_env"]
        == m["diagnostics"]["token_env"]
        == g["observability"]["probe_token_env"]
        == "TIANSHU_DIAGNOSTICS_TOKEN",
        "diagnostics_mapping_changed",
    )
    require(
        p["tls"]
        == {
            "certificate_file": "/etc/tianshu/tls/server.pem",
            "private_key_file": "/etc/tianshu/tls/server.key",
        },
        "platform_tls_paths",
    )
    require(
        p["web"]["origin"] == site["web_origin"]
        and p["web"]["static_directory"] == "/srv/tianshu/web",
        "web_origin_or_static_mapping",
    )
    require(
        set(p["web"]["password_hash"]) == {"$password_env"}
        if isinstance(p["web"]["password_hash"], dict)
        else False,
        "password_env_required",
    )
    admin = p["principals"][p["web"]["principal"]]
    require(
        admin["kind"] == "operator"
        and admin["service"] == "platform"
        and {"source.register", "source.dispatch", "mapping.prepare", "origin.issue"}
        <= set(admin["actions"])
        and bool(p["web"]["input_entries"]),
        "web_admin_invalid",
    )
    for entry in p["web"]["input_entries"]:
        require(
            p["input_entries"][entry]["owner"] == p["web"]["principal"],
            "web_entry_owner",
        )
    features = {f["id"]: f for f in manifest["features"]}
    if manifest["schema_version"] == "1.1.0":
        require(
            p.get("model_origin_renewal_http") is True
            and g.get("platform_origin_renewal") is True,
            "explicit_source_renewal_required",
        )
        require(
            any(
                item["id"] == "model-origin-renewal/v1"
                for item in manifest["contracts"]
            ),
            "renewal_contract_required",
        )
        require(
            p["entries"]["config-entry"]["routes"]
            == [
                {
                    "caller": "gateway",
                    "receiver": "platform",
                    "purpose": "config.snapshot",
                }
            ],
            "dedicated_config_entry_required",
        )
        require(
            c.get("automatic_memory_candidates") is False,
            "explicit_memory_candidate_disable_required",
        )
        if p["web"]["dialogue_enabled"]:
            require(
                manifest["products"]["companion"]["source"]["commit"]
                == "e94b609099365f75ca933d9fed03cdfbc83ec235",
                "reviewed_memory_disable_version_required",
            )
    require(
        features["web_text_dialogue"]["enabled"] == p["web"]["dialogue_enabled"],
        "feature_configuration_mismatch",
    )
    # This adapter intentionally has no consumer/archiver/knowledge/GPU/channel implementation.
    require(
        not any(
            f["enabled"]
            for f in manifest["features"]
            if f["id"] not in {"web_text_dialogue", "central_logging"}
        ),
        "unsupported_enabled_feature",
    )
    require(
        not c.get("images")
        and not c.get("proactive")
        and not c.get("direct")
        and not c.get("life_writing")
        and not g.get("native_enabled")
        and not p.get("home")
        and not p.get("assets")
        and not p.get("native_config_http"),
        "out_of_scope_configuration",
    )
    require(
        p["core"]["base_url"] == endpoint("companion") and p["core"]["ca_file"] == CA,
        "platform_core_mapping",
    )
    for service in ("platform", "platform_sender", "memory", "gateway"):
        target = "platform" if service == "platform_sender" else service
        require(
            c["services"][service]["url"] == endpoint(target)
            and c["services"][service]["ca_file"] == CA,
            "companion_peer_mapping",
        )
    require(
        "nonebot" not in c["callers"] and "nonebot" not in c["services"],
        "channel_not_enabled",
    )
    require(
        c["callers"]["platform"]["issuer"] == "platform"
        and c["callers"]["platform"]["origin_service"] == "platform",
        "core_issuer_mapping",
    )
    for owner, product, suffix in (
        ("platform", "platform", "/internal/v1/source-access/read"),
        ("core", "companion", "/internal/v1/source-facts/read"),
    ):
        require(
            m["source_sync"][owner]["url"] == endpoint(product) + suffix
            and m["source_sync"][owner]["ca_file"] == CA,
            "memory_source_mapping",
        )
        require(
            isinstance(m["source_sync"][owner]["token"], dict)
            and set(m["source_sync"][owner]["token"]) == {"$env"},
            "memory_secret_reference",
        )
    caller = m["callers"]["companion"]
    require(
        caller["issuer"] == "platform"
        and caller["issuer_url"]
        == endpoint("platform") + "/internal/v1/origins/resolve"
        and caller["issuer_ca_file"] == CA,
        "memory_issuer_mapping",
    )
    require(
        isinstance(caller["token"], dict)
        and set(caller["token"]) == {"$env"}
        and isinstance(caller["issuer_token"], dict)
        and set(caller["issuer_token"]) == {"$env"},
        "memory_secret_reference",
    )
    require(
        m["source_sync"]["recovery_path"]
        == "/srv/tianshu/memory.sqlite.source-guard.json",
        "memory_guard_mapping",
    )
    require(g["platform_base_url"] == endpoint("platform"), "gateway_platform_mapping")
    targets = [t for t in g["targets"] if t["base_url"] == endpoint("platform")]
    require(
        len(targets) == 1
        and targets[0]["addresses"] == [site["service_ips"]["platform"]],
        "gateway_registered_ip_mismatch",
    )
    for target in g["targets"]:
        require(
            urlsplit(target["base_url"]).scheme == "https"
            and not target.get("allow_private_http"),
            "upstream_tls_required",
        )
        require(bool(target["addresses"]), "reviewed_upstream_ips_required")
        for address in target["addresses"]:
            ipaddress.ip_address(address)
    require(
        p["core"]["token_env"] == c["callers"]["platform"]["token_env"],
        "peer_credential_mismatch",
    )
    require(
        c["services"]["memory"]["token_env"] == caller["token"]["$env"],
        "peer_credential_mismatch",
    )
    grants = [client for client in g["clients"] if client["service"] == "companion"]
    require(
        len(grants) == 1 and c["config_version"] == grants[0]["config_version"],
        "model_version_mismatch",
    )
    require(
        grants[0].get("internal") is True, "companion_internal_model_grant_required"
    )
    require(
        g["secret_references"][grants[0]["credential_ref"]]
        == c["services"]["gateway"]["token_env"],
        "peer_credential_mismatch",
    )
    require(
        m["source_sync"]["core"]["token"]["$env"]
        == c["callers"]["memory"]["token_env"],
        "peer_credential_mismatch",
    )
    # Product grants, action lists and source scopes remain product-owned; check the reciprocal
    # service identities here, and require the product preflight + release runner before promotion.
    for env, service, action in (
        (c["services"]["platform"]["token_env"], "companion", "origin.resolve"),
        (c["services"]["platform_sender"]["token_env"], "companion", "dialogue.send"),
        (caller["issuer_token"]["$env"], "memory", "origin.resolve"),
        (m["source_sync"]["platform"]["token"]["$env"], "memory", "source.current"),
        (
            g["secret_references"][g["platform_credential_ref"]],
            "gateway",
            "config.snapshot",
        ),
    ):
        require(
            any(
                x["token_env"] == env
                and x["service"] == service
                and action in x["actions"]
                for x in p["principals"].values()
            ),
            "platform_peer_grant_missing",
        )


def prepare_inputs(manifest, inputs_path, environ=None):
    environ = os.environ if environ is None else environ
    site = load_inputs(inputs_path)
    root = inputs_path.parent
    configs = {p: read_json(inside(root, site["config_files"][p])) for p in PRODUCTS}
    validate_configs(configs, site, manifest)
    resolved, environments, materials = {}, {}, {}
    business_values, diagnostic_values, credential_names = set(), [], {}
    password_reference = configs["platform"]["web"]["password_hash"]["$password_env"]
    for product in PRODUCTS:
        tls = site["tls"][product]
        shape(tls, {"certificate", "key", "ca", "provenance"}, "tls_input_invalid")
        require(
            tls["provenance"] in {"operator_supplied", "isolated_test"},
            "tls_provenance_required",
        )
        names = next(
            s["tls_server_names"] for s in manifest["services"] if s["id"] == product
        )
        if product == "platform":
            names = [*names, urlsplit(site["web_origin"]).hostname]
        paths = {k: inside(root, tls[k]) for k in ("certificate", "key", "ca")}
        tls_check(paths["certificate"], paths["key"], paths["ca"], names)
        materials[product] = {k: v.read_bytes() for k, v in paths.items()}
        values = {}
        for name in references(configs[product]):
            source = (
                site["diagnostics_env"][product]
                if name == "TIANSHU_DIAGNOSTICS_TOKEN"
                else name
            )
            value = environ.get(source)
            require(
                isinstance(value, str) and bool(value), "required_environment_missing"
            )
            if name == password_reference:
                require(
                    12 <= len(value) <= 256 and all(ord(ch) >= 32 for ch in value),
                    "admin_password_length_or_control_character",
                )
            else:
                require(
                    16 <= len(value) <= 4096
                    and all(33 <= ord(ch) <= 126 for ch in value)
                    and "'" not in value
                    and "\\" not in value,
                    "unsafe_environment_value",
                )
            values[name] = value
            if name == "TIANSHU_DIAGNOSTICS_TOKEN":
                diagnostic_values.append(value)
            else:
                business_values.add(value)
                previous = credential_names.get(value)
                require(
                    previous is None or previous == name, "distinct_credentials_reused"
                )
                credential_names[value] = name
        resolved[product] = resolve_config(configs[product], values)
        # Memory uses literal fields in its private JSON. Password is reduced to scrypt and
        # neither raw password nor unused memory credentials are copied into container env.
        needed = references(resolved[product])
        environments[product] = {k: v for k, v in values.items() if k in needed}
    require(
        len(set(diagnostic_values)) == 4
        and not set(diagnostic_values) & business_values,
        "diagnostics_credentials_not_independent",
    )
    # Every caller uses the same explicit trust bundle; the gateway's aiohttp honors
    # SSL_CERT_FILE. Include public roots in this bundle when a public model endpoint is used.
    require(
        len({v["ca"] for v in materials.values()}) == 1, "shared_trust_bundle_required"
    )
    return site, resolved, environments, materials
