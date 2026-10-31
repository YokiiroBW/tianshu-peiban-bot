"""Optional external-host bridge. It never connects to the product database."""

from .host import Host, SDKFailure

__all__ = ["Host", "SDKFailure"]
