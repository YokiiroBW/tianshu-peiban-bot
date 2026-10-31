"""Owner invalidation hooks, invoked inside the source owner's invalidation transaction."""
from .codec import document,source_refs


def intersects_one(values,changed,kind):
    for item in source_refs(values):
        if item.source_id!=changed.source_id or item.revision!=changed.revision:
            continue
        if kind=="fragment_forgotten":
            own,removed=item.locator,changed.locator
            bounds=(own.get("start"),own.get("end"),removed.get("start"),removed.get("end"))
            if all(type(value) in (int,float) for value in bounds) and own.get("unit")==removed.get("unit"):
                if bounds[1]<=bounds[2] or bounds[3]<=bounds[0]:
                    continue
        return True
    return False


def intersects(values,changes,kind):
    for item in source_refs(values):
        for changed in changes.get((item.source_id,item.revision),()):
            if intersects_one((item,),changed,kind):
                return True
    return False


def changes_for(source_refs):
    changes={}
    for ref in source_refs:
        changes.setdefault((ref.source_id,ref.revision),[]).append(ref)
    return changes,list({ref.source_id for ref in source_refs})
