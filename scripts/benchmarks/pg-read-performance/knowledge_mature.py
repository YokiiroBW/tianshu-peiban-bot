"""Existing-data reads after the full workload; no seed/statistics maintenance."""

import json

from common import arguments, emit, measure
from fastapi.testclient import TestClient
from knowledge_fixture import SECRET
from knowledge_sql import fail, signature
from tianshu_memory.domain import canonical
from tianshu_memory.knowledge import KnowledgeApplication
from tianshu_memory.knowledge_http import ACTION_PATH, create_app
from tianshu_memory.store import Store


def main():
    args, _, _ = arguments()
    # Trial23 is the last complete four-way data set. It now includes the same
    # synthetic dependency/import workload on every backend, with no reseeding.
    directory = (
        args.config.parent / "runs" / f"knowledge-{args.variant}-23-{args.backend}"
    )
    path = directory / "private.json"
    config = json.loads(path.read_bytes())
    with_store = Store.from_config(config, service="knowledge")
    try:
        assert with_store.backend == args.backend
        with with_store.transaction() as db:
            block_count = db.execute(
                "SELECT COUNT(*) FROM knowledge_blocks"
            ).fetchone()[0]
            statistics = (
                [
                    list(row)
                    for row in db.native(
                        "SELECT relname,n_live_tup,last_analyze::text,last_autoanalyze::text "
                        "FROM pg_stat_all_tables WHERE schemaname=current_schema() "
                        "AND relname IN ('knowledge_index','knowledge_blocks') ORDER BY relname"
                    )
                ]
                if args.backend == "postgresql"
                else None
            )
    finally:
        with_store.close()

    app = KnowledgeApplication(path)

    def query(text):
        result = app.execute(
            {
                "operation": "query",
                "project_id": "demo",
                "arguments": {"text": text, "budget_bytes": 32768},
            },
            client="writer",
            credential=SECRET,
        )
        assert len(canonical(result).encode()) <= 32768
        return result

    expected = {
        "exact": signature(query("unique0x0")),
        "cohort": signature(query("cohort3")),
    }
    assert len(query("unique0x0")["blocks"]) == 1
    assert len(query("cohort3")["blocks"]) == 20
    results = []
    try:
        for key, text in (("exact", "unique0x0"), ("cohort", "cohort3")):
            results.append(
                measure(
                    "knowledge_mature_" + key,
                    lambda _, q=text: query(q),
                    40,
                    check=lambda value, k=key: (
                        signature(value) == expected[k] or fail()
                    ),
                )
            )
    finally:
        close = getattr(app, "close", None)
        if close:
            close()
    with TestClient(
        create_app(path, "writer", 8130), base_url="http://127.0.0.1:8130"
    ) as client:

        def http_query(text):
            response = client.post(
                ACTION_PATH,
                json={
                    "operation": "query",
                    "project_id": "demo",
                    "arguments": {"text": text, "budget_bytes": 32768},
                },
                headers={"Authorization": "Bearer " + SECRET},
            )
            assert response.status_code == 200
            return response.json()

        for key, text in (("exact", "unique0x0"), ("cohort", "cohort3")):
            results.append(
                measure(
                    "knowledge_mature_http_" + key,
                    lambda _, q=text: http_query(q),
                    20,
                    check=lambda value, k=key: (
                        signature(value) == expected[k] or fail()
                    ),
                )
            )
    emit(
        args,
        "knowledge_mature",
        results,
        existing_data_trial=23,
        existing_block_count=block_count,
        statistics_before_timing=statistics,
        manual_analyze_or_maintenance=False,
        logical_result_signatures=expected,
        models_called=0,
    )


if __name__ == "__main__":
    main()
