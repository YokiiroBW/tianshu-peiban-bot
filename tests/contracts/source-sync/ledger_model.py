"""Small executable contract state model, deliberately not a product SourceAuthority.

One JSON state cell in temporary SQLite models atomicity/replay; this is not a proposed
schema migration and does not prove production SQL, coverage queries, auth or HTTP work.
"""
import copy
import json
import sqlite3

from relations import by_key, canonical, check_drafts, check_event, digest, metadata, require, source_key


def text_domain(scope):
    return "text-dialogue/v1:" + canonical(scope)


def public_domain(actor):
    return "profile-memory/v1:public:" + actor


def group_domain(actor, conversation):
    return "profile-memory/v1:group:" + canonical([actor, conversation])


class Ledger:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("CREATE TABLE IF NOT EXISTS contract_state(id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
        state = dict(revision=0, sources={}, versions={}, groups={}, jobs={}, events={}, turns={}, writes={}, effects=0, core_head=None)
        self.db.execute("INSERT OR IGNORE INTO contract_state VALUES(1,?)", (canonical(state),))

    def close(self):
        self.db.close()

    def state(self):
        return json.loads(self.db.execute("SELECT body FROM contract_state WHERE id=1").fetchone()[0])

    def mutate(self, operation, *, crash=False):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self.state()
            result = operation(state)
            self.db.execute("UPDATE contract_state SET body=? WHERE id=1", (canonical(state),))
            if crash:
                raise RuntimeError("synthetic crash after writes, before COMMIT")
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def ticket(self, scope, profile=False):
        state = self.state()
        keys = set()
        if not profile:
            keys.update(k for k, row in state["sources"].items() if row["fact"]["scope"] == scope)
        domains = {public_domain(scope["actor_id"])}
        if scope["audience"] == "group":
            domains.add(group_domain(scope["actor_id"], scope["conversation_id"]))
        for group in state["groups"].values():
            relevant = group["domain"] in domains if profile else group["domain"] == text_domain(scope)
            if group["active"] and relevant:
                keys.update(group["keys"])
        return dict(revision=state["revision"], keys=sorted(keys))

    def sync(self, ticket, facts, access, head, *, crash=False):
        def apply(state):
            require(ticket["revision"] == state["revision"], "changed_local")
            incoming, grants = by_key(facts), by_key(access)
            require(set(ticket["keys"]) <= set(incoming) and set(incoming) == set(grants), "coverage")
            prior_head = state["core_head"]
            if prior_head is not None:
                require(head["generation"] == prior_head["generation"] and head["sequence"] >= prior_head["sequence"], "recovery_required")
            changed_domains, changed = set(), False
            for key, fact in incoming.items():
                require(fact["state"] != "missing", "dependency_unavailable")
                grant = {k: v for k, v in grants[key].items() if k != "admission_digest"}
                old = state["sources"].get(key)
                new = dict(fact=metadata(fact), access=grant, epoch=1, suppressed=False)
                if old:
                    require(fact["source"]["message_key"]["revision"] >= old["fact"]["source"]["message_key"]["revision"], "stale_source")
                    require(not (old["fact"]["state"] == "withdrawn" and fact["state"] == "active"), "resurrection")
                    if fact["source"]["message_key"]["revision"] == old["fact"]["source"]["message_key"]["revision"]:
                        immutable = ("source", "scope", "author", "binding_version", "accepted_origin", "accepted_at", "ingest_sequence", "kind", "content_digest")
                        require(all(fact[k] == old["fact"][k] for k in immutable), "idempotency_conflict")
                    if old["fact"] == new["fact"] and old["access"] == new["access"]:
                        continue
                    new.update(epoch=old["epoch"] + 1, suppressed=old["suppressed"])
                    changed_domains.add(text_domain(old["fact"]["scope"]))
                    changed_domains.add(text_domain(fact["scope"]))
                    self.invalidate(state, key, changed_domains)
                state["sources"][key] = new
                changed = True
            self.bump(state, changed_domains)
            if changed:
                state["revision"] += 1
            state["core_head"] = copy.deepcopy(head)
            return state["revision"]
        return self.mutate(apply, crash=crash)

    @staticmethod
    def active(row):
        return not row["suppressed"] and row["fact"]["state"] == "active" and row["access"]["state"] == "allowed" and row["fact"]["classification"]["value"] in {"real", "fictional"}

    @staticmethod
    def invalidate(state, key, domains):
        for group in state["groups"].values():
            if key in group["keys"] and group["active"]:
                group["active"] = False
                domains.add(group["domain"])
        for job in state["jobs"].values():
            if key in job["snapshot"] and job["state"] == "pending":
                job["state"] = "stale_source"

    @staticmethod
    def bump(state, domains):
        for domain in domains:
            state["versions"][domain] = state["versions"].get(domain, 1) + 1

    def suppress(self, key):
        # This transition represents an already verified exact Memory confirmation.
        def apply(state):
            row = state["sources"][key]
            if row["suppressed"]:
                return
            row["suppressed"] = True
            row["epoch"] += 1
            domains = {text_domain(row["fact"]["scope"])}
            self.invalidate(state, key, domains)
            self.bump(state, domains)
            state["revision"] += 1
        self.mutate(apply)

    def seed_group(self, group_id, keys, domain):
        # A previously approved complete projection/group, not a share approval endpoint.
        def apply(state):
            require(all(self.active(state["sources"][k]) for k in keys), "stale_source")
            state["groups"][group_id] = dict(keys=keys, domain=domain, active=True)
            self.bump(state, {domain})
            state["revision"] += 1
        self.mutate(apply)

    def probe(self, revision, scope, known=None, *, profile=False):
        state = self.state()
        require(revision == state["revision"], "changed_local")
        if profile:
            version = state["versions"].get(public_domain(scope["actor_id"]), 1)
            if scope["audience"] == "group":
                version += state["versions"].get(group_domain(scope["actor_id"], scope["conversation_id"]), 1) - 1
        else:
            version = state["versions"].get(text_domain(scope), 1)
        require(known is None or known == version, "scope_changed")
        return version

    def consume(self, event, turn):
        def apply(state):
            event_hash = digest(event)
            old = state["events"].get(event["event_id"])
            if old:
                require(old["digest"] == event_hash, "idempotency_conflict")
                return old["job"]
            signature = digest({k: v for k, v in event.items() if k != "event_id"})
            turn_key = canonical([event["aggregate_id"], event["input_revision"]])
            if turn_key in state["turns"]:
                prior = state["turns"][turn_key]
                require(prior["signature"] == signature, "idempotency_conflict")
                state["events"][event["event_id"]] = dict(digest=event_hash, job=prior["job"])
                return prior["job"]
            facts = [dict(row["fact"], content=None) for row in state["sources"].values()]
            check_event(event, turn, facts)
            require(event["scope_version"] == state["versions"].get(text_domain(event["scope"]), 1), "scope_changed")
            keys = [source_key(s) for s in event["sources"]]
            require(all(self.active(state["sources"][k]) for k in keys), "stale_source")
            job_id = "job:" + digest(turn_key)
            state["jobs"][job_id] = dict(event=event, state="pending", snapshot={k: state["sources"][k]["epoch"] for k in keys}, write=None)
            state["events"][event["event_id"]] = dict(digest=event_hash, job=job_id)
            state["turns"][turn_key] = dict(signature=signature, job=job_id)
            state["revision"] += 1
            return job_id
        return self.mutate(apply)

    def commit(self, job_id, drafts, turn, *, crash=False):
        def apply(state):
            job = state["jobs"][job_id]
            if job["write"] is not None:
                require(job["write"]["digest"] == digest(drafts), "idempotency_conflict")
                return job["write"]["result"]
            require(job["state"] == "pending", "stale_source")
            event = job["event"]
            require(event["scope_version"] == state["versions"].get(text_domain(event["scope"]), 1), "scope_changed")
            for key, epoch in job["snapshot"].items():
                require(self.active(state["sources"][key]) and state["sources"][key]["epoch"] == epoch, "stale_source")
            facts = [dict(row["fact"], content=None) for row in state["sources"].values()]
            check_event(event, turn, facts)
            check_drafts(dict(job_id=job_id, drafts=drafts), event, facts)
            source_writes = [canonical([k, state["sources"][k]["fact"]["source"]["message_key"]["revision"], event["scope"]]) for k in job["snapshot"]]
            if any(k in state["writes"] for k in source_writes):
                result = "duplicate_source"
            else:
                for index, draft in enumerate(drafts):
                    state["groups"][job_id + ":" + str(index)] = dict(keys=list(job["snapshot"]), domain=text_domain(draft["scope"]), active=True, units=draft["units"])
                    state["effects"] += draft["relationship_delta"] or 0
                for key in source_writes:
                    state["writes"][key] = job_id
                result = "committed" if drafts else "skipped"
            job.update(state=result, write=dict(digest=digest(drafts), result=result))
            state["revision"] += 1
            return result
        return self.mutate(apply, crash=crash)
