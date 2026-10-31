"""Reading progress and explicit, short-lived co-reading grants owned by the book library."""

import math
import uuid

from ...contracts import Fault, canonical, require




class Reading:
    def __init__(self, books):
        self.books, self.repo = books, books.repo
        self.live_sessions = {}

    def text(self, body):
        book = self.books.catalog.book(body.get("book_id"))
        chapter = self.books.catalog.chapter(book["book_id"], body.get("chapter_id"))
        require(chapter, "book_content_unavailable", 409)
        if body.get("audio_job"):
            job = self.repo.get("book_audio_job", body["audio_job"])
            require(
                job
                and job["state"] == "ready"
                and job["book_id"] == book["book_id"]
                and job["chapter_id"] == chapter["chapter_id"],
                "not_found",
                404,
            )
            chapter = {**chapter, "content": job["text"], "content_hash": job["content_hash"]}
        require(
            chapter["content_hash"] == body.get("content_hash"), "book_reading_text_changed", 409
        )
        return book, chapter

    def open(self, owner, body, db):
        current = self.repo._document(db, "book_reader", owner)
        # Compare-and-swap prevents a delayed opening tab from replacing a newer reader.
        require(
            (current or {}).get("revision", 0) == body.get("expected_revision"),
            "version_conflict",
            409,
        )
        row = {
            "owner": owner,
            "reader_id": uuid.uuid4().hex,
            "book_id": body["book_id"],
            "chapter_id": body["chapter_id"],
            "content_hash": body["content_hash"],
            "audio_job": body.get("audio_job"),
            "offset": 0,
            "seconds": 0,
            "sequence": 0,
            "sharing": False,
            "closed": False,
            "updated_at": self.repo.clock(),
        }
        prior = self.repo._document(db, "book_reading_progress", owner + ":" + body["book_id"])
        if (
            prior
            and prior["chapter_id"] == row["chapter_id"]
            and prior["content_hash"] == row["content_hash"]
        ):
            row.update(offset=prior["offset"], seconds=prior["seconds"])
        return self.repo.put("book_reader", owner, row, db=db)

    def update(self, owner, body, chapter, scope, db):
        row = self.repo._document(db, "book_reader", owner)
        require(
            row and row["reader_id"] == body.get("reader_id") and not row["closed"],
            "book_reading_session_changed",
            409,
        )
        require(
            type(body.get("sequence")) is int and body["sequence"] > row["sequence"],
            "book_reading_stale_progress",
            409,
        )
        offset, seconds = body.get("offset", row["offset"]), body.get("seconds", row["seconds"])
        require(
            type(offset) is int and 0 <= offset <= len(chapter["content"]), "invalid_input", 400
        )
        require(
            type(seconds) in {int, float} and math.isfinite(seconds) and 0 <= seconds <= 86400,
            "invalid_input",
            400,
        )
        require(type(body.get("sharing", False)) is bool, "invalid_input", 400)
        sharing = body.get("sharing", False)
        require(
            not sharing or scope and scope["person_id"] and scope["conversation_kind"]=="private",
            "book_reading_private_required",
            409,
        )
        changed_grant = sharing != row["sharing"] or scope != row.get("scope")
        result = self.repo.put(
            "book_reader",
            owner,
            {
                **row,
                "offset": offset,
                "seconds": seconds,
                "scope": scope,
                "binding": {k: body.get(k) for k in ("conversation", "actor")},
                "sharing": sharing,
                "sequence": body["sequence"],
                "grant_id": uuid.uuid4().hex if changed_grant else row.get("grant_id"),
                "updated_at": self.repo.clock(),
            },
            db=db,
        )
        self.repo.put(
            "book_reading_progress",
            owner + ":" + row["book_id"],
            {
                k: result[k]
                for k in (
                    "book_id",
                    "chapter_id",
                    "content_hash",
                    "offset",
                    "seconds",
                    "updated_at",
                )
            },
            db=db,
        )
        return result

    def close(self, owner, body, db):
        row = self.repo._document(db, "book_reader", owner)
        if row and row["reader_id"] == body.get("reader_id") and not row["closed"]:
            self.repo.put(
                "book_reader",
                owner,
                {**row, "sharing": False, "closed": True, "updated_at": self.repo.clock()},
                db=db,
            )
        return {"closed": True}

    async def web(self,web,operation,body,session):
        from tianshu.runtime.serialization import wire
        local=self.books.media.local;owner=session.person_id or session.principal_id
        if operation=="view":return {"reader":await local(self.repo.get,"book_reader",owner),"choices":[{"actor_id":session.actor_id,"scope_ids":list(session.scope_ids),"audience":list(session.audience)}] if session.actor_id else [],"progress":await local(self.repo.get,"book_reading_progress",owner+":"+body.get("book_id",""))}
        if operation=="close":return await web.web.receipt(body,"books/reading/close",session,lambda db:self.close(owner,body,db))
        if operation=="open":
            await local(self.text,body)
            return await web.web.receipt(body,"books/reading/open",session,lambda db:{"reader":self.open(owner,body,db)})
        if operation=="progress":
            row=await local(self.repo.get,"book_reader",owner)
            require(row and row["reader_id"]==body.get("reader_id"),"book_reading_session_changed",409)
            _,chapter=await local(self.text,row)
            scope={key:wire(session)[key] for key in ("actor_id","person_id","scope_ids","audience","access_epoch","conversation_id","conversation_kind")} if body.get("sharing") else None
            return await web.web.receipt(body,"books/reading/progress",session,lambda db:{"reader":self.update(owner,body,chapter,scope,db)})
        raise Fault("not_found",404)

class ReadingAccess:
    def __init__(self,reading,authority,sources):self.reading,self.authority,self.sources=reading,authority,sources
    def bind(self,reading):
        if self.reading is not None and self.reading is not reading:
            raise RuntimeError("reading_already_bound")
        self.reading=reading
    async def read(self,context,grant_id=None):
        from tianshu.runtime.serialization import wire
        await self.authority.check(context)
        if self.reading is None:raise Fault("capabilities_starting",503)
        owner=context.person_id or context.principal_id;reading=self.reading
        row=await reading.books.media.local(reading.repo.get,"book_reader",owner)
        response={"active":None,"grant_current":False,"observed_at":reading.repo.clock(),"catalog":[],"catalog_total":0}
        scope={key:wire(context)[key] for key in ("actor_id","person_id","scope_ids","audience","access_epoch","conversation_id","conversation_kind")}
        if not row or row.get("closed") or not row.get("sharing") or row.get("scope")!=scope or row.get("updated_at",0)+75<reading.repo.clock() or (grant_id and row.get("grant_id")!=grant_id):return response
        book,chapter=await reading.books.media.local(reading.text,row)
        response.update(grant_current=True,active={"grant_id":row["grant_id"],"book_id":row["book_id"],"chapter_id":row["chapter_id"],"title":book["metadata"]["title"],"content_hash":row["content_hash"],"offset":row["offset"],"seconds":row["seconds"],"excerpt":chapter["content"][max(0,row["offset"]-6000):row["offset"]],"representation":"reader_position_only","experience":"human_reading_observation"})
        if response["active"]["excerpt"]:
            source=await self.sources.capture(context,locator={"kind":"human_reading_observation","book_id":row["book_id"],"chapter_id":row["chapter_id"],"content_hash":row["content_hash"],"offset":row["offset"],"grant_id":row["grant_id"]},raw=response["active"]["excerpt"].encode(),mime_type="text/plain")
            response["source_refs"]=[wire(source)]
        await self.authority.check(context)
        return response
