"""Compare protocol choices after the same native PG write history.

This is a separate causal diagnostic, not an installed candidate comparison.
The sequential experiment removes only the pipeline context from the installed
checkout AST; templates, preparation, current checks and cleanup remain intact.
"""

import ast
from contextlib import closing
import hashlib
import importlib.util
import inspect
import textwrap
import time

from common import arguments, emit, settings
from memory_sql import signature
from memory_write_fixture import contract_files, open_fixture, seed_fixture
from tianshu_storage.runtime import Database, StorageRuntime


class Sequential(ast.NodeTransformer):
    def __init__(self):
        self.removed = 0

    def visit_With(self, node):
        self.generic_visit(node)
        if len(node.items) == 1:
            expression = node.items[0].context_expr
            if (
                isinstance(expression, ast.Call)
                and isinstance(expression.func, ast.Attribute)
                and isinstance(expression.func.value, ast.Name)
                and expression.func.value.id == "raw"
                and expression.func.attr == "pipeline"
            ):
                self.removed += 1
                return node.body
        return node


def sequential_checkout():
    original = StorageRuntime._checkout
    transformer = Sequential()
    tree = transformer.visit(ast.parse(textwrap.dedent(inspect.getsource(original))))
    assert transformer.removed == 1
    ast.fix_missing_locations(tree)
    namespace = dict(original.__globals__)
    exec(compile(tree, "<sequential-checkout-diagnostic>", "exec"), namespace)
    return namespace["_checkout"], hashlib.sha256(
        ast.unparse(tree).encode()
    ).hexdigest()


def main():
    args, private, _ = arguments()
    assert args.backend == "postgresql" and args.variant == "optimized"
    path = args.config.parent / "code/baseline_runtime.py"
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == private["baseline_checkout_sha256"]
    )
    spec = importlib.util.spec_from_file_location(
        "tianshu_storage._baseline_runtime", path
    )
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    sequence, sequence_hash = sequential_checkout()
    methods = {
        "original": (baseline.Database.connect, baseline.StorageRuntime._checkout),
        "pipeline": (Database.connect, StorageRuntime._checkout),
        "sequential": (Database.connect, sequence),
    }
    contracts = contract_files(args.config.parent)
    directory = args.config.parent / "runs" / f"checkout-sequence-{args.trial}"
    directory.mkdir(parents=True)
    # Import an empty native schema first, then create all history through PG.
    fixture = seed_fixture(directory, contracts, "local", size=0)
    config = dict(
        database_path=fixture.store.path,
        **settings(private, f"seq{args.trial}", args.backend),
    )
    state = fixture.state()
    fixture.store.close()
    fixture = open_fixture(state, contracts, config)
    try:
        for offset in range(0, 300, 10):
            assert (
                fixture.commit(fixture.prepare(range(offset, offset + 10)))["state"]
                == "committed"
            )
        for index in range(300, 330):
            assert fixture.commit(fixture.prepare([index]))["state"] == "committed"
        for index in range(330, 365):
            assert fixture.full_flow(index)[1]["state"] == "committed"
        assert fixture.counts()["groups"] == 365
        # Match the keyword-before-field history without timing these calls.
        for _ in range(100):
            fixture.selection("Python")

        def checkout():
            with closing(fixture.store.database.connect(isolation_level=None)) as db:
                return tuple(
                    db.native("SELECT current_schema(),pg_backend_pid()").fetchone()
                )

        operations = {
            "checkout_and_select": checkout,
            "authorized_exact": lambda: fixture.selection("field:bench-0"),
            "authorized_zero_budget": lambda: fixture.selection("Python", budget=0),
        }
        guard_before = hashlib.sha256(
            fixture.store.recovery_path.read_bytes()
        ).hexdigest()
        reports = {}
        for case, operation in operations.items():

            def content(result):
                return result if case == "checkout_and_select" else signature(result)

            expected = content(operation())
            blocks = []
            for block in range(3):
                samples = []
                for index in range(-6, 6 if args.smoke else 60):
                    sides = list(methods)
                    rotation = (index + block) % 3
                    sides = sides[rotation:] + sides[:rotation]
                    if (index // 3 + block) % 2:
                        sides.reverse()
                    values = {}
                    for side in sides:
                        Database.connect, StorageRuntime._checkout = methods[side]
                        cpu_start, start = time.thread_time_ns(), time.perf_counter_ns()
                        result = operation()
                        elapsed = time.perf_counter_ns() - start
                        cpu = time.thread_time_ns() - cpu_start
                        assert content(result) == expected
                        values[side] = dict(wall_ns=elapsed, thread_cpu_ns=cpu)
                    if index >= 0:
                        samples.append(dict(index=index, order=sides, samples=values))
                blocks.append(dict(block=block + 1, trios=samples))
            reports[case] = blocks
        assert (
            hashlib.sha256(fixture.store.recovery_path.read_bytes()).hexdigest()
            == guard_before
        )
        Database.connect, StorageRuntime._checkout = methods["pipeline"]
        with fixture.store.transaction() as db:
            assert db.native("SHOW synchronous_commit").fetchone()[0] == "on"
            session = tuple(
                db.native(
                    "SELECT pg_backend_pid(),COUNT(*) FROM pg_prepared_statements "
                    "WHERE statement LIKE 'SELECT pg_catalog.set_config%' "
                    "OR statement LIKE '%.__tianshu_storage_binding WHERE singleton OPERATOR%'"
                ).fetchone()
            )
        pool_stats = fixture.store.runtime._pool.get_stats()
        counts = fixture.counts()
    finally:
        Database.connect, StorageRuntime._checkout = methods["pipeline"]
        fixture.store.close()
    emit(
        args,
        "checkout_sequence_compare",
        [],
        separate_causal_diagnostic=True,
        installed_candidate_not_changed=True,
        runtime_override="Only remove raw.pipeline context from installed checkout AST",
        sequential_ast_sha256=sequence_hash,
        native_pg_write_history=True,
        counts=counts,
        untimed_session=dict(
            backend_pid=session[0],
            internal_prepared_templates=session[1],
            pool_stats=pool_stats,
        ),
        same_pool_schema_guard_and_product=True,
        unchanged_guard_verified=True,
        source_and_permission_checks_preserved=True,
        hook_assignment_outside_timer=True,
        warmup_trios_per_case_block=6,
        cases=reports,
        models_called=0,
    )


if __name__ == "__main__":
    main()
