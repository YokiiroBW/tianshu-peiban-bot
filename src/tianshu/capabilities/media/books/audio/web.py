"""Authenticated book speech commands with existing durable command receipts."""

from ....contracts import Fault, digest, require


async def route(web, operation, body, session):
    audio = web.books.audio
    local, repo = audio.local, audio.repo
    if operation == "settings":
        return await audio.settings()
    if operation == "settings/save":
        return await audio.books.media.capabilities.speech.save_profile(body,session)
    if operation == "view":
        return await audio.view(body.get("book_id"))
    if operation == "manifest":
        return await audio.manifest(body.get("job_id"))
    if operation in {"estimate", "generate"}:
        selected, rows, estimate = await audio.prepare(body)
        if operation == "estimate":
            return estimate
        require(body.get("profile_id") == estimate["profile_id"], "version_conflict", 409)
        chapters = await local(audio.books.catalog.directory, body["book_id"])
        require(type(body.get("auto_new", False)) is bool, "invalid_input", 400)

        def generate(db):
            result = audio.ledger.enqueue(rows, db)
            old = repo._document(db, "book_audio_plan", body["book_id"])
            initial = (
                old["initial_chapters"]
                if old and old["profile_id"] == estimate["profile_id"] and old["auto_new"]
                else [c["chapter_id"] for c in chapters]
            )
            repo.put(
                "book_audio_plan",
                body["book_id"],
                {
                    "book_id": body["book_id"],
                    "profile": selected,
                    "profile_id": estimate["profile_id"],
                    "auto_new": body.get("auto_new", False),
                    "paused": False,
                    "initial_chapters": initial,
                    "context": __import__("tianshu.runtime.serialization",fromlist=["wire"]).wire(session),
                },
                db=db,
            )
            return {**result, "missing_text": estimate["missing_text"]}

        result = await web.web.receipt(body, "books/audio/generate", session, generate)
        audio.wake.set()
        return result
    if operation == "control":
        require(
            body.get("action") in {"pause", "resume", "auto_on", "auto_off"}, "invalid_input", 400
        )
        chapters = await local(audio.books.catalog.directory, body.get("book_id"))

        def control(db):
            plan = repo._document(db, "book_audio_plan", body.get("book_id"))
            require(plan, "not_found", 404)
            change = (
                {"paused": body["action"] == "pause"}
                if body["action"] in {"pause", "resume"}
                else {"auto_new": body["action"] == "auto_on"}
            )
            if body["action"] == "auto_on" and not plan["auto_new"]:
                change["initial_chapters"] = [c["chapter_id"] for c in chapters]
            return {
                "plan": repo.put(
                    "book_audio_plan",
                    plan["book_id"],
                    {**plan, **change},
                    body.get("expected_revision"),
                    db=db,
                )
            }

        result = await web.web.receipt(body, "books/audio/control", session, control)
        audio.wake.set()
        return result
    if operation == "retry":
        result = await web.web.receipt(
            body, "books/audio/retry", session, lambda db: audio.ledger.retry(body, db)
        )
        audio.wake.set()
        return result
    raise Fault("not_found", 404)
