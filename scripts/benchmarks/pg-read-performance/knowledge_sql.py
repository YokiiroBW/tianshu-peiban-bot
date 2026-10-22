"""Knowledge dispatch with warm app and fresh app, including auth and live file checks."""

import hashlib
import json

from common import arguments, emit, measure, pg_calls, settings, sqlite_calls
from fastapi.testclient import TestClient
from knowledge_fixture import SECRET, knowledge
from tianshu_memory.contracts import Contracts
from tianshu_memory.domain import canonical
from tianshu_memory.knowledge import KnowledgeApplication
from tianshu_memory.knowledge_http import ACTION_PATH, create_app
from tianshu_memory.store import Store


def main():
    args, private, _ = arguments()
    directory = (
        args.config.parent
        / "runs"
        / f"knowledge-{args.variant}-{args.trial}-{args.backend}"
    )
    directory.mkdir(parents=True)
    contracts = Contracts(args.config.parent / "contracts" / "text-dialogue" / "v1")
    run, root, store, path, config = knowledge(directory, contracts)
    store.close()
    config.update(settings(private, f"k{args.namespace}", args.backend))
    path.write_text(canonical(config), encoding="utf-8")
    selected = Store.from_config(config, service="knowledge")
    assert selected.backend == args.backend
    selected.close()
    blocks_per_file = 10 if args.smoke else 100
    for document in range(2):
        name = f"synthetic-{document}.txt"
        (root / name).write_text(
            "\n".join(
                f"cohort{index % 10} unique{document}x{index} synthetic evidence line {index}."
                for index in range(blocks_per_file)
            )
            + "\n",
            encoding="utf-8",
        )
        result = run(
            "import",
            {
                "key": f"seed-{document}",
                "kind": "file",
                "locator": name,
                "expected_version": 0,
                "groups": [
                    {"start": i + 1, "end": i + 1, "depends_on": []}
                    for i in range(blocks_per_file)
                ],
            },
        )
        assert result["status"] == "imported" and result["version"] == 1

    def query(text, budget=32768):
        result = run("query", {"text": text, "budget_bytes": budget})
        assert len(canonical(result).encode()) <= budget
        return result

    exact, cohort = query("unique0x0"), query("cohort3")
    assert len(exact["blocks"]) == 1 and len(cohort["blocks"]) == blocks_per_file // 5
    expected = {"exact": signature(exact), "cohort": signature(cohort)}
    results = [
        measure(
            "knowledge_authorized_exact_query",
            lambda _: query("unique0x0"),
            3 if args.smoke else 40,
            cpu_clocks=args.cpu_clocks,
            check=lambda value: signature(value) == expected["exact"] or fail(),
        ),
        measure(
            "knowledge_authorized_cohort20_query",
            lambda _: query("cohort3"),
            3 if args.smoke else 40,
            cpu_clocks=args.cpu_clocks,
            check=lambda value: signature(value) == expected["cohort"] or fail(),
        ),
    ]

    def cold(_):
        app = KnowledgeApplication(path)
        try:
            return app.execute(
                {
                    "operation": "query",
                    "project_id": "demo",
                    "arguments": {"text": "unique0x0", "budget_bytes": 32768},
                },
                client="writer",
                credential=SECRET,
            )
        finally:
            close = getattr(app, "close", None)
            if close:
                close()

    results.append(
        measure(
            "knowledge_fresh_application_query",
            cold,
            3 if args.smoke else 20,
            cpu_clocks=args.cpu_clocks,
            check=lambda value: signature(value) == expected["exact"] or fail(),
        )
    )
    (root / "closure.txt").write_text(
        "closuretoken condition\nsecond required dependency\nthird required negation\n",
        encoding="utf-8",
    )
    closure = run(
        "import",
        {
            "key": "closure",
            "kind": "file",
            "locator": "closure.txt",
            "expected_version": 0,
            "groups": [
                {"start": 1, "end": 1, "depends_on": [1]},
                {"start": 2, "end": 2, "depends_on": [2]},
                {"start": 3, "end": 3, "depends_on": []},
            ],
        },
    )
    assert closure["status"] == "imported" and closure["blocks"] == 1
    whole = query("closuretoken", 4096)
    assert len(whole["blocks"]) == 1 and whole["blocks"][0]["spans"] == [
        [1, 1],
        [2, 2],
        [3, 3],
    ]
    bounded = query("closuretoken", 256)
    assert not bounded["blocks"] and "budget" in bounded["omissions"]
    results.append(
        measure(
            "knowledge_indivisible_group_small_budget",
            lambda _: query("closuretoken", 256),
            3 if args.smoke else 20,
            cpu_clocks=args.cpu_clocks,
            check=lambda value: (
                not value["blocks"] and "budget" in value["omissions"] or fail()
            ),
        )
    )
    # New small files; no idempotent replay or source network fetch is timed.
    count = 2 if args.smoke else 10
    for index in range(count):
        (root / f"new-{index}.txt").write_text(
            "synthetic imported evidence\n" * 5, encoding="utf-8"
        )

    def import_new(index):
        return run(
            "import",
            {
                "key": f"new-{index}",
                "kind": "file",
                "locator": f"new-{index}.txt",
                "expected_version": 0,
                "groups": [
                    {"start": i + 1, "end": i + 1, "depends_on": []} for i in range(5)
                ],
            },
        )

    results.append(
        measure(
            "knowledge_import_5blocks",
            import_new,
            count,
            cpu_clocks=args.cpu_clocks,
            warmup=0,
            check=lambda value: value["status"] == "imported" or fail(),
        )
    )
    signatures = {
        "exact": signature(exact),
        "cohort": signature(cohort),
        "closure": signature(whole),
        "small_budget": signature(bounded),
    }
    sql_profile = {
        "exact": pg_calls(lambda: query("unique0x0"), args.backend),
        "cohort": pg_calls(lambda: query("cohort3"), args.backend),
    }
    sqlite_profile = {
        "exact": sqlite_calls(lambda: query("unique0x0"), args.backend),
        "cohort": sqlite_calls(lambda: query("cohort3"), args.backend),
    }
    # The exported native fixture owns an app in its closure. Close it only after
    # every measured operation has truly ended; original code has no close API.
    for cell in run.__closure__ or ():
        app = cell.cell_contents
        if isinstance(app, KnowledgeApplication) and hasattr(app, "close"):
            app.close()
    # Restricted HTTP/ASGI entry: serialization, admission and per-request state
    # stay inside this client container; no host port or production endpoint.
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
            assert response.status_code == 200, response.text
            result = response.json()
            assert len(canonical(result).encode()) <= 32768
            return result

        for name, text in (("exact", "unique0x0"), ("cohort", "cohort3")):
            results.append(
                measure(
                    "knowledge_http_" + name + "_query",
                    lambda _, q=text: http_query(q),
                    3 if args.smoke else 20,
                    cpu_clocks=args.cpu_clocks,
                    check=lambda value, key=name: (
                        signature(value) == expected[key] or fail()
                    ),
                )
            )
        sql_profile["http_exact"] = pg_calls(
            lambda: http_query("unique0x0"), args.backend
        )
        sqlite_profile["http_exact"] = sqlite_calls(
            lambda: http_query("unique0x0"), args.backend
        )
    emit(
        args,
        "knowledge",
        results,
        seed_blocks=blocks_per_file * 2,
        models_called=0,
        logical_result_signatures=signatures,
        untimed_pg_adapter_calls=sql_profile,
        untimed_sqlite_trace=sqlite_profile,
        timed_boundary="Native KnowledgeApplication.execute plus explicit HTTP/ASGI cases; warm app retains only its supported Store lifecycle; fresh app includes creation and close; auth and live file checks included",
    )


def signature(result):
    normalized = json.loads(canonical(result))
    for block in normalized["blocks"]:
        assert block["reference"]["version"] == 1
        assert len(block["reference"]["hash"]) == 64 and block["source_id"].startswith(
            "source:"
        )
        # Import timestamps differ across isolated runs; all other provenance,
        # source/reference hashes, versions, spans and omissions are comparable.
        assert block["provenance"].pop("imported_at")
    ordered = hashlib.sha256(canonical(normalized).encode()).hexdigest()
    normalized["blocks"].sort(key=lambda block: canonical(block))
    unordered = hashlib.sha256(canonical(normalized).encode()).hexdigest()
    return {"ordered": ordered, "content": unordered}


def fail():
    raise AssertionError("synthetic knowledge result mismatch")


if __name__ == "__main__":
    main()
