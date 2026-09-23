"""Explicit operator-selected text provider for bounded acceptance, never auto-discovery."""

import ipaddress
from urllib.parse import urlsplit

from manifest import require


def validate(value):
    require(
        isinstance(value, dict)
        and set(value)
        == {"base_url", "model_id", "addresses", "secret", "text_probe_passed"},
        "real_provider_shape",
    )
    url = urlsplit(value["base_url"])
    require(
        url.scheme == "https"
        and url.hostname
        and not url.username
        and not url.password
        and not url.query
        and not url.fragment
        and url.port in (None, 443)
        and value["text_probe_passed"] is True,
        "real_provider_https_and_prior_text_probe_required",
    )
    require(
        isinstance(value["model_id"], str) and 0 < len(value["model_id"]) <= 128,
        "real_provider_model_invalid",
    )
    require(
        isinstance(value["addresses"], list)
        and 1 <= len(value["addresses"]) <= 32
        and all(ipaddress.ip_address(a).is_global for a in value["addresses"]),
        "real_provider_public_reviewed_addresses_required",
    )
    # Single-quoted Compose env format: forbid quote/newline/interpolation ambiguity.
    secret = value["secret"]
    require(
        isinstance(secret, str)
        and 1 <= len(secret) <= 4096
        and all(33 <= ord(c) <= 126 and c not in "'\\$" for c in secret),
        "real_provider_secret_format_invalid",
    )
    return value
