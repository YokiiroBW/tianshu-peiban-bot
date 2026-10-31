"""Resolve canonical model media through its explicit source or artifact owner."""

from tianshu.runtime.errors import AppError
from tianshu.types import MediaRef, SourceRef


class OwnedMedia:
    def __init__(self, sources, artifacts):
        self.sources, self.artifacts = sources, artifacts

    async def reference(self, context, requested):
        if requested.media_id.startswith("artifact:"):
            artifact = await self.artifacts.reference(context, requested.media_id)
            actual, refs = artifact.media, artifact.source_refs
        elif requested.media_id.startswith("source:"):
            pieces = requested.media_id.split(":")
            if len(pieces) != 3 or not pieces[2].isdigit():
                raise AppError("invalid_source_media")
            ref = SourceRef(pieces[1], int(pieces[2]))
            source = await self.sources.read(context, ref, {"max_characters": 1})
            if not source["media"]:
                raise AppError("source_media_unavailable", status=404)
            actual, refs = MediaRef(**source["media"]), (ref,)
        else:
            raise AppError("controlled_media_owner_required", status=403)
        if actual is None:
            raise AppError("artifact_media_unavailable", status=404)
        if actual.sha256 != requested.sha256 or actual.uri != requested.uri:
            raise AppError("media_reference_mismatch", status=403)
        # MIME, duration and dimensions come from the owner, never from model arguments.
        return actual, tuple(refs)
