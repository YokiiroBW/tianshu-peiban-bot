"""Bounded local native SQLite check; no PG, container, image proof or NAS.

The explicit source export supplies the same selected AST fixture bodies as the
controller. Certificates must be the existing disposable native TLS fixture.
"""

import argparse
import ast
import copy
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


class NamedChecks(unittest.TestCase):
    contract_directory = certificates = None

    def test_native_hundred_name_owners_and_complete_two_subject_boundary(self):
        import memory_named_context as study

        with tempfile.TemporaryDirectory(prefix="ts292-named-") as temporary:
            results, details = study.run_study(
                Path(temporary),
                study.Contracts(self.contract_directory),
                {},
                "sqlite",
                "namedcheck",
                self.certificates,
                count=1,
                warmup=1,
                cpu_clocks=True,
            )
        self.assertEqual(details["registered_name_owners"], 100)
        self.assertEqual(details["native_fact_groups"], 101)
        self.assertEqual(details["returned_scope_checks"], dict(unknown=1, known=2))
        self.assertEqual(details["wrong_actor"], "forbidden")
        self.assertTrue(
            details["projection_ownership_proof"]["swapped_refs_contract_valid"]
        )
        self.assertEqual(
            details["projection_ownership_proof"]["swapped_refs"],
            "rejected_before_normalization",
        )
        self.assertEqual(details["storage_before"], details["storage_after"])
        self.assertEqual(details["storage_before"]["durability"]["journal_mode"], "wal")
        self.assertEqual(details["storage_before"]["durability"]["synchronous"], 2)
        for row in results:
            self.assertEqual(row["operations"], 1)
            self.assertEqual(len(row["latencies_ms"]), 1)
            self.assertEqual(len(row["process_cpu_ms"]), 1)
            self.assertEqual(len(row["thread_cpu_ms"]), 1)
            self.assertFalse(row["cpu_clock_protocol"]["profiling"])
        for counts in details["untimed_call_counts"].values():
            self.assertEqual(counts["name_matches"], 100)
            self.assertEqual(counts["fresh_revision"], 6)
            self.assertEqual(
                counts["owner_rpc_counts"],
                dict(origin=1, snapshot=1, current=1, head=1),
            )
            self.assertTrue(
                any(
                    view == dict(entries=100, current_names=100, incomplete=0)
                    for view in counts["name_views"]
                )
            )
        # Only counts/signatures leave the synthetic fixture, not request bodies.
        print(
            json.dumps(
                {
                    "native_fixture": dict(owners=100, groups=101),
                    "untimed_call_counts": details["untimed_call_counts"],
                    "logical_result_signatures": details["logical_result_signatures"],
                },
                sort_keys=True,
            )
        )

    def test_diagnostic_methods_restore_after_failure(self):
        import memory_named_context as study

        body = study.recall_language._reference_body
        prepared = getattr(study.recall_language, "NamedReferenceQuery", None)
        owner, method = (
            (prepared, "for_name")
            if prepared
            else (study.recall_relevance, "named_reference")
        )
        named = getattr(owner, method)
        current = study.NameView.current
        revision = study.SourceAuthority.revision

        def failed():
            raise RuntimeError("synthetic diagnostic failure")

        with self.assertRaisesRegex(RuntimeError, "synthetic diagnostic failure"):
            study.untimed_counts(failed)
        self.assertIs(study.recall_language._reference_body, body)
        self.assertIs(getattr(owner, method), named)
        self.assertIs(study.NameView.current, current)
        self.assertIs(study.SourceAuthority.revision, revision)


def fixtures(source):
    spec = importlib.util.spec_from_file_location(
        "sync_fixture", source / "tests/source_sync_harness.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_fixture"] = module
    spec.loader.exec_module(module)
    from tianshu_memory.domain import canonical, fingerprint

    group = types.ModuleType("group_fixture")
    group.__dict__.update(
        copy=copy, json=json, canonical=canonical, fingerprint=fingerprint
    )
    for filename, names in (
        ("test_candidate_extraction.py", {"PATH"}),
        ("test_automatic_facts.py", {"initialize", "add"}),
        ("test_group_context.py", {"owner", "query", "recalled"}),
    ):
        path = source / "tests" / filename
        parsed = ast.parse(path.read_text(encoding="utf-8"))
        selected = [
            node
            for node in parsed.body
            if (isinstance(node, ast.FunctionDef) and node.name in names)
            or (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id in names
                    for target in node.targets
                )
            )
        ]
        assert len(selected) == len(names)
        exec(
            compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"),
            group.__dict__,
        )
    sys.modules["group_fixture"] = group


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--contract-directory", type=Path, required=True)
    parser.add_argument("--certificates", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root / "src"))
    fixtures(args.source_root)
    NamedChecks.contract_directory, NamedChecks.certificates = (
        args.contract_directory,
        args.certificates,
    )
    unittest.main(argv=[sys.argv[0]], verbosity=2)


if __name__ == "__main__":
    main()
