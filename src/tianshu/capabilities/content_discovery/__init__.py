"""Content candidates over existing owners; no body or reading-history store."""

from .service import Discovery
from .media import MediaCatalog

__all__ = ["Discovery", "MediaCatalog"]
