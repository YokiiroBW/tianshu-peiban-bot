"""Read-only task-centre adapter; execution remains owned by the plugin ledger."""

import json

from ..contracts import require

STATUS = {
    "accepted": ("accepted", "queued"),
    "running": ("in_progress", "executing"),
    "succeeded": ("observed", "completed"),
    "unknown": ("unknown", "unknown"),
    "failed": ("failed", "failed"),
    "cancelled": ("failed", "cancelled"),
    "interrupted": ("failed", "interrupted"),
}
STATE_SQL = "CASE WHEN (document::jsonb #>> '{effect}')='unknown' THEN 'unknown' ELSE (document::jsonb #>> '{execution}') END"


class PluginTasks:
    def __init__(self, host):
        self.host, self.repo = host, host.repository

    def record(self, identifier):
        require(identifier.startswith("plugin:"), "invalid_input", 400)
        document = self.repo.call(identifier[7:])
        return self.row(document) if document else None

    def row(self, document):
        return {
            "task_id": "plugin:" + document["id"],
            "created_at": document["created_at"],
            "updated_at": document["updated_at"],
            "state": "unknown" if document["effect"] == "unknown" else document["execution"],
            "document": self.host.executions.public(document),
        }

    def count(self):
        with self.repo.transaction(False) as db:
            return db.execute('SELECT COUNT(*) FROM capabilities.actions WHERE instance_id IS NOT NULL').fetchone()[0]

    def history(self, *, after, limit, states):
        clauses, parameters = ["instance_id IS NOT NULL"], []
        if after:
            clauses.append("(created_at<%s OR (created_at=%s AND id<%s))")
            parameters.extend([after[0], after[0], after[1][7:]])
        if states:
            clauses.append(STATE_SQL + " IN (" + ",".join('%s' for _ in states) + ")")
            parameters.extend(sorted(states))
        query = 'SELECT document FROM capabilities.actions'
        if clauses:
            query += ' WHERE ' + " AND ".join(clauses)
        query += ' ORDER BY created_at DESC,id DESC LIMIT %s'
        parameters.append(limit + 1)
        with self.repo.transaction(False) as db:
            rows = [self.row(json.loads(row[0])) for row in db.execute(query, parameters)]
        return rows[:limit], len(rows) > limit
