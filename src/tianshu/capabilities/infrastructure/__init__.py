"""Registered infrastructure observations; SSH ownership stays with the node executor."""

from .config import configuration
from .runtime import Infrastructure

__all__ = ["Infrastructure", "configuration"]
