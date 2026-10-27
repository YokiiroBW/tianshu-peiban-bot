"""Only synthetic aux metadata; no helper PG/Docker/backup entrypoint invoked."""

import ast
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).with_name("cold_backup_all_stopped.py")


def aux_nodes(tree):
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "cold_backup"
    )
    return [
        n
        for n in function.body
        if isinstance(n, ast.FunctionDef)
        and n.name == "check_aux"
        or isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "aux_template" for t in n.targets)
    ]


class AllStoppedTests(unittest.TestCase):
    def setUp(self):
        self.cores = {"core-" + str(i) for i in range(10)}
        self.original = {name: {} for name in self.cores}
        self.observed = {}
        for index, name in enumerate(("onebot", "comic-inputs", "comic-publisher")):
            identifier = str(index) * 64
            mounts = [
                {
                    "Source": "/synthetic/data/" + name,
                    "Destination": "/data",
                    "RW": True,
                }
            ]
            self.original[name] = {
                "Id": identifier,
                "Image": "sha256:" + "f" * 64,
                "Mounts": mounts,
                "HostConfig": {"RestartPolicy": {"Name": "no"}},
                "State": {"Running": False, "Status": "exited", "ExitCode": 0},
            }
            self.observed[identifier] = {
                "id": identifier,
                "image": "sha256:" + "f" * 64,
                "mounts": deepcopy(mounts),
                "running": False,
                "state": "exited",
                "exit": 0,
                "restart": "no",
                "paused": False,
                "restarting": False,
                "dead": False,
            }
        self.calls = []

    def check(self):
        def require(condition, code):
            if not condition:
                raise RuntimeError(code)

        def command(arguments):
            self.calls.append(arguments)
            return SimpleNamespace(
                stdout=json.dumps(self.observed[arguments[-1]]).encode()
            )

        namespace = {
            "json": json,
            "old_owners": self.original,
            "B": SimpleNamespace(
                DOCKER=["synthetic-docker"], command=command, require=require
            ),
            "U": SimpleNamespace(
                OWNERS=self.cores,
                canonical_mounts=lambda value: sorted(
                    json.dumps(item, sort_keys=True) for item in value
                ),
            ),
        }
        module = ast.fix_missing_locations(
            ast.Module(body=aux_nodes(ast.parse(SOURCE.read_bytes())), type_ignores=[])
        )
        exec(compile(module, str(SOURCE), "exec"), namespace)
        namespace["check_aux"]()
        return namespace["aux_template"]

    def test_all_three_aux_normal_exit_and_restart_no_pass_metadata_only(self):
        template = self.check()
        self.assertEqual(len(self.calls), 3)
        self.assertIn(".State.Status", template)
        self.assertNotIn(".Config.Env", template)
        self.assertTrue(
            all(call[1:3] == ["inspect", "--format"] for call in self.calls)
        )

    def test_changed_identity_mount_or_any_non_cold_state_is_refused(self):
        original = deepcopy(self.observed)
        for field, wrong in (
            ("id", "other"),
            ("image", "other"),
            ("mounts", []),
            ("running", True),
            ("state", "created"),
            ("exit", 1),
            ("restart", "unless-stopped"),
            ("paused", True),
            ("restarting", True),
            ("dead", True),
        ):
            for name in ("onebot", "comic-inputs", "comic-publisher"):
                self.observed = deepcopy(original)
                self.observed[self.original[name]["Id"]][field] = wrong
                with (
                    self.subTest(name=name, field=field),
                    self.assertRaisesRegex(
                        RuntimeError, "auxiliary_identity_or_stopped_state_changed"
                    ),
                ):
                    self.check()

    def test_supplied_original_baseline_must_also_be_cold_and_restart_no(self):
        original = deepcopy(self.original)
        for field, wrong in (("Running", True), ("Status", "running"), ("ExitCode", 1)):
            self.original = deepcopy(original)
            self.original["comic-inputs"]["State"][field] = wrong
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.check()
        self.original = deepcopy(original)
        self.original["comic-publisher"]["HostConfig"]["RestartPolicy"]["Name"] = (
            "always"
        )
        with self.assertRaises(RuntimeError):
            self.check()

    @unittest.skipUnless(
        os.environ.get("TIANSHU_COLD_HELPER_ORIGINAL"),
        "explicit historical helper not selected",
    )
    def test_all_code_outside_aux_template_and_checker_is_identical_ast(self):
        def outside_aux(path):
            tree = ast.parse(Path(path).read_bytes())
            function = next(
                n
                for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "cold_backup"
            )
            omitted = aux_nodes(tree)
            function.body = [n for n in function.body if n not in omitted]
            return ast.dump(tree)

        self.assertEqual(
            outside_aux(SOURCE), outside_aux(os.environ["TIANSHU_COLD_HELPER_ORIGINAL"])
        )


if __name__ == "__main__":
    unittest.main()
