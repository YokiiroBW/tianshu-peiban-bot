"""Stable person display identity shared by NFO and server observations."""

NFO_PROFILE = "bilibili-creators-v1"


def director_name(name, mid):
    displayed = name.translate(str.maketrans({",": "，", "|": "｜", ";": "；"}))
    return f"{displayed}（B站 UID {mid}）"


def named_uploaders(snapshot):
    return [
        {**creator, "director_name": director_name(creator["name"], creator["mid"])}
        for creator in snapshot["creators"]
        if creator.get("role") == "uploader" and creator.get("name", "").strip()
    ]
