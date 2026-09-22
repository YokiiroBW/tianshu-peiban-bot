"""First-install authority through the product CLI; never write product databases directly."""

import json
import re
import subprocess
import time
from datetime import datetime

from manifest import digest, no_links, read_json, require, write_json


def remaining(receipt, *, now=None, minimum_seconds=30):
    require(
        type(minimum_seconds) is int and minimum_seconds > 0, "bootstrap_budget_invalid"
    )
    require(
        isinstance(receipt.get("assertion_ref"), str)
        and re.fullmatch(r"origin:[0-9a-f]{32}", receipt["assertion_ref"]),
        "product_assertion_required",
    )
    expiry = datetime.fromisoformat(receipt["expires_at"].replace("Z", "+00:00"))
    require(expiry.tzinfo is not None, "assertion_expiry_timezone_required")
    seconds = expiry.timestamp() - (time.time() if now is None else now)
    require(seconds >= minimum_seconds, "assertion_expired_or_budget_insufficient")
    return int(seconds)


def local_action(
    python, product, settings, environment, credential_env, action, data, work
):
    """Called only while this harness owns a stopped, newly created isolated deployment."""
    require(
        action in {"publish", "issue", "view-config"}, "unsupported_bootstrap_action"
    )
    require(
        bool(environment.get(credential_env)), "registered_admin_credential_missing"
    )
    request = work / (action + "-input.json")
    require(not request.exists(), "bootstrap_action_already_attempted")
    write_json(request, data)
    request.chmod(0o600)
    env = dict(environment, PYTHONPATH=str(product), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [
            str(python),
            "-B",
            "-m",
            "services.platform",
            "--settings",
            str(settings),
            "local",
            "--credential-env",
            credential_env,
            action,
            "--input",
            str(request),
        ],
        env=env,
        capture_output=True,
        timeout=30,
    )
    require(result.returncode == 0, "product_bootstrap_cli_failed")
    value = json.loads(result.stdout)
    return value


def first_install(
    python, product, settings, environment, publication, work, *, minimum_seconds=30
):
    """Public CLI orchestration for a NEW synthetic deployment, before any service is started.

    No online renewal, no retry after uncertain publication, no secret output. The caller keeps
    the returned receipt in its private environment and must enforce the remaining lifetime.
    """
    config = read_json(settings)
    database = no_links(config["database_path"])
    require(not database.exists(), "fresh_platform_database_required")
    require(config["mode"] == "service_https", "https_authority_required")
    require(
        config["web"]["username"].startswith("synthetic-"), "synthetic_admin_required"
    )
    require(
        set(config["providers"]) == {"provider-synthetic"},
        "synthetic_provider_required",
    )
    from urllib.parse import urlsplit
    import ipaddress

    provider = config["providers"]["provider-synthetic"]
    address = urlsplit(provider["base_url"])
    require(
        address.scheme == "https"
        and ipaddress.ip_address(address.hostname).is_loopback,
        "synthetic_provider_loopback_required",
    )
    principal = config["principals"][config["web"]["principal"]]
    require(
        principal["kind"] == "operator" and principal["service"] == "platform",
        "registered_admin_required",
    )
    require(not work.exists(), "new_bootstrap_directory_required")
    work.mkdir(mode=0o700)
    # A marker persists even when an uncertain command fails: this is not an automatic retry.
    write_json(
        work / "attempt.json",
        {"settings_sha256": digest(settings.read_bytes()), "state": "started"},
    )
    published = local_action(
        python,
        product,
        settings,
        environment,
        principal["token_env"],
        "publish",
        publication,
        work,
    )
    receipt = local_action(
        python,
        product,
        settings,
        environment,
        principal["token_env"],
        "issue",
        {"entry_id": "config-entry"},
        work,
    )
    budget = remaining(receipt, minimum_seconds=minimum_seconds)
    write_json(
        work / "result.json",
        {
            "state": "authority_initialized",
            "issuer": "product_platform_cli",
            "published_result_sha256": digest(
                json.dumps(published, sort_keys=True).encode()
            ),
            "expires_at": receipt["expires_at"],
            "remaining_seconds_at_issue": budget,
            "publication_sha256": digest(
                json.dumps(publication, sort_keys=True).encode()
            ),
            "automatic_renewal": False,
            "real_model": "not_configured",
        },
    )
    return receipt
