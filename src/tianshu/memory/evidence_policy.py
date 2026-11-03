"""Eligibility of source parts for an attributable human statement."""


def attributable_statement(evidence):
    """A human upload is an origin, not proof that its contents are self-confirmed.

    This checks evidence type only. Deciding whether an eligible statement
    actually confirms the proposed fact remains the semantic extractor's job.
    Unmarked historical text keeps its existing source-authority semantics.
    """
    locator = evidence.get("locator") or {}
    metadata = locator.get("metadata") or {}
    media = evidence.get("media") or {}
    part = evidence.get("part_kind") or locator.get("part_kind")
    kind = evidence.get("evidence_kind") or metadata.get("evidence_kind")
    if kind in {"image", "text_image", "reference", "ocr"}:
        return False
    if part and part not in {"text", "audio", "voice", "speech", "transcription"}:
        return False
    if metadata.get("ocr") or metadata.get("is_ocr") or metadata.get("text_origin") in {"ocr", "image"}:
        return False
    mime_type = str(media.get("mime_type", "")).casefold()
    if mime_type.startswith(("image/", "video/")) or media.get("kind") in {"image", "video"}:
        return False
    return True
