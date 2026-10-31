"""Provider credentials cannot name deployment master keys or other provider namespaces."""

import re

from tianshu.runtime.errors import AppError


def provider_reference(provider_id, reference, configured=None):
    own = "sealed:provider:" + provider_id
    if reference == own:
        return reference
    allowed = (configured or {}).get(provider_id)
    if (
        isinstance(allowed, str)
        and re.fullmatch(r"(?:env:)?TIANSHU_PROVIDER_[A-Z0-9_]+", allowed)
        and isinstance(reference, str)
        and reference.removeprefix("env:") == allowed.removeprefix("env:")
    ):
        return allowed
    raise AppError("provider_credential_reference_forbidden", status=403)
