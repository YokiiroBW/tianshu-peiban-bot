"""Concrete starting points from existing intentions, not invented URLs or accounts."""
from tianshu.conversation.codec import signature
from .planner import valid_arguments


def choices(data):
    discover=next((item for item in data["capabilities"] if item["id"]=="content.discover"),None)
    offers=[]
    topics=[(row["id"],row["title"],row["id"]) for row in data["goals"] if row["state"]=="active"]
    topics.extend((row["id"],row.get("title") or row.get("content"),None) for row in data["interests"])
    for identifier,title,goal_id in topics:
        if not title or not discover:
            continue
        arguments={"query":title[:300],"mode":"explore","limit":3}
        if not valid_arguments(discover["input_schema"],arguments):
            continue
        offers.append({"id":"explore:"+signature({"id":identifier,"query":title}),
            "basis_id":identifier,"activity":{"title":"看看关于“"+title[:80]+"”的新内容","kind":"exploration",
                "goal_id":goal_id,"capability_id":"content.discover","arguments":arguments,"duration_minutes":30}})
    writing=next((item for item in data["capabilities"] if item["id"]=="writing.request"),None)
    if writing:
        for work in data.get("writing",()):
            chapter=next((row for row in work["chapters"] if row["state"]=="planned" and not row["current_revision"]),None)
            if chapter is None:
                continue
            args={"id":chapter["id"],"expected_revision":chapter["revision"]}
            if valid_arguments(writing["input_schema"],args):
                goal=next((row["id"] for row in data["goals"] if row["state"]=="active" and row.get("criteria",{}).get("work_id")==work["id"]),None)
                offers.append({"id":"write:"+chapter["id"],"basis_id":work["id"],"activity":{
                    "title":"继续写“"+work["title"][:80]+"”："+chapter["title"][:80],"kind":"writing","goal_id":goal,
                    "capability_id":"writing.request","arguments":args,"duration_minutes":60}})
    return offers[:8]
