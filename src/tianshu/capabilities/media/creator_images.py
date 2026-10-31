"""Observe/upload only MID-qualified Directors attached to the matched Emby item."""

from urllib.parse import quote

from ..contracts import Fault
from .creators import named_uploaders
from .publication import matching_image


async def verify_creator_images(remote, cache, server, job, item, checked):
    results = []
    avatars = {avatar["mid"]: avatar for avatar in job.get("creator_avatars", [])}
    for creator in named_uploaders(job["snapshot"]):
        result = {
            "mid": creator["mid"],
            "name": creator["director_name"],
            "person_id": None,
            "state": "pending",
            "code": "director_pending",
        }
        people = [
            person
            for person in item.get("People", [])
            if person.get("Type") == "Director"
            and person.get("Name") == creator["director_name"]
            and person.get("Id")
        ]
        if len(people) != 1:
            if len(people) > 1:
                result["code"] = "director_identity_conflict"
            results.append(result)
            continue
        person_id = str(people[0]["Id"])
        result["person_id"] = person_id
        avatar = avatars.get(creator["mid"])
        if not avatar or avatar["state"] != "ready" or cache is None:
            result.update(
                state="unavailable",
                code=avatar.get("code", "creator_avatar_pending")
                if avatar
                else "creator_avatar_pending",
            )
            results.append(result)
            continue
        key = (person_id, avatar["sha256"])
        if key in checked:
            results.append(checked[key])
            continue
        try:
            raw = await cache.media.local(cache.read, avatar)
            matched = False
            try:
                observed = await remote.image(server, quote(person_id, safe=""))
                matched = await cache.media.local(matching_image, raw, observed)
            except Fault as error:
                if error.code != "library_image_unavailable":
                    raise
            if not matched:
                await remote.upload_image(server, person_id, raw, avatar["extension"])
                observed = await remote.image(server, quote(person_id, safe=""))
                matched = await cache.media.local(matching_image, raw, observed)
            result.update(
                state="verified" if matched else "mismatch",
                code="director_image_verified" if matched else "director_image_mismatch",
                sha256=avatar["sha256"],
            )
        except (Fault, OSError) as error:
            result.update(
                state="unavailable",
                code=error.code if isinstance(error, Fault) else "creator_avatar_unavailable",
            )
        checked[key] = result
        results.append(result)
    return results
