from tianshu.life.publications import diary,chapter,version


def test_published_reader_projection_excludes_new_draft_and_internal_material():
    row={"id":"diary","actor_id":"actor","day":"2026-10-31","published_revision":3,
         "current_revision":5,"revision":9,"state":"generating","generation_recipe":{"episodes":["private"]},
         "context":{"private":"scope"},"content":"Published text","published_at":"2026-10-31T12:00:00Z",
         "disclosure_ref":{"source_id":"publication","revision":2,"locator":{}}}
    projected={"diary":diary(row),"version":version(row)}
    assert projected["diary"]["revision"]==3
    assert projected["version"]["content"]=="Published text"
    assert "generation_recipe" not in projected["diary"]
    assert "context" not in projected["diary"]
    assert "current_revision" not in projected["diary"]
    assert "recipe" not in projected["version"]


def test_published_chapter_metadata_is_the_published_snapshot():
    row={"id":"chapter","work_id":"work","title":"Private new title","position":9,
         "published_revision":2,"publication_metadata":{"title":"Published title","position":1},
         "disclosure_ref":{"source_id":"publication","revision":1,"locator":{}}}
    assert chapter(row)["title"]=="Published title"
    assert chapter(row)["position"]==1
