"""Pure matching of selected dependency versions and forgotten ranges; grants no access."""

from tianshu.types import SourceRef


def _refs(values):
    return (SourceRef(**value) if isinstance(value,dict) else value for value in values or ())


def intersects_one(values,changed,kind):
    for item in _refs(values):
        if (item.source_id,item.revision)!=(changed.source_id,changed.revision):
            continue
        if kind=="fragment_forgotten":
            own,removed=item.locator or {},changed.locator or {}
            bounds=(own.get("start"),own.get("end"),removed.get("start"),removed.get("end"))
            if all(type(value) in (int,float) for value in bounds) and own.get("unit")==removed.get("unit"):
                if bounds[1]<=bounds[2] or bounds[3]<=bounds[0]:
                    continue
        return True
    return False


def intersects(values,changes,kind):
    return any(intersects_one((item,),changed,kind) for item in _refs(values)
        for changed in changes.get((item.source_id,item.revision),()))


def changes_for(source_refs):
    changes={}
    for ref in source_refs:
        changes.setdefault((ref.source_id,ref.revision),[]).append(ref)
    return changes,sorted({ref.source_id for ref in source_refs})
