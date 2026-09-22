"""POSIX metadata counterexamples for real permission_checks; NOT Linux execution."""

import stat
import sys
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "deploy/tianshu"))
import bundle  # noqa: E402
from manifest import PRODUCTS, Refused  # noqa: E402


class Node:
    def __init__(self, tree, name):
        self.tree, self.name = tree, name

    def stat(self):
        self.tree.visited.add(self.name)
        return self.tree.metadata[self.name]

    def rglob(self, _):
        return [
            Node(self.tree, name)
            for name in self.tree.metadata
            if name.startswith(self.name + "/")
        ]

    def iterdir(self):
        return [
            Node(self.tree, name)
            for name in self.tree.metadata
            if str(PurePosixPath(name).parent) == self.name
        ]


class PermissionChecksTests(unittest.TestCase):
    def setUp(self):
        self.metadata, self.visited = {}, set()
        for product in PRODUCTS:
            for directory in (
                f"data/{product}",
                f"logs/{product}",
                f"config/{product}",
            ):
                self.add(directory, 0o750, directory=True)
            self.add(f"config/{product}/settings.json", 0o640)
            self.add(f"config/{product}/tls", 0o750, directory=True)
            for name in ("server.key", "server.pem", "ca.pem"):
                self.add(f"config/{product}/tls/{name}", 0o640)
        # A host operator owns the non-secret inputs, and private env files. Only the
        # actual mounted roots/descendants are visible inside the container.
        for name in (
            "contracts",
            "contracts/text-dialogue",
            "contracts/text-dialogue/v1",
        ):
            self.add(name, 0o755, uid=1200, gid=1200, directory=True)
        self.add("contracts/text-dialogue/v1/manifest.json", 0o644, uid=1200, gid=1200)
        self.add("tools/runtime_guard.py", 0o644, uid=1200, gid=1200)
        self.add("private", 0o700, uid=1200, gid=1200, directory=True)
        for product in PRODUCTS:
            self.add(f"private/{product}.env", 0o600, uid=1200, gid=1200)

    def add(self, name, mode, *, uid=10001, gid=10001, directory=False):
        self.metadata[name] = SimpleNamespace(
            st_mode=(stat.S_IFDIR if directory else stat.S_IFREG) | mode,
            st_uid=uid,
            st_gid=gid,
        )

    def check(self):
        # Only filesystem/stat access and the host kind are substituted. The production
        # checker, mode-class selection, mount scope and refusal paths all execute.
        with (
            patch.object(bundle, "os", SimpleNamespace(name="posix")),
            patch.object(bundle, "inside", lambda root, name: Node(self, name)),
            patch.object(bundle, "no_links", lambda node: node),
        ):
            bundle.permission_checks(None)

    def test_owner_access_and_public_inputs_from_another_uid(self):
        self.check()
        self.assertTrue(set(self.metadata) <= self.visited)

    def test_group_readable_config_and_contracts_are_valid(self):
        for name, item in self.metadata.items():
            if name.startswith("config/") or name.startswith("contracts"):
                item.st_uid, item.st_gid = 1200, 10001
                item.st_mode = (
                    stat.S_IFDIR | 0o750
                    if stat.S_ISDIR(item.st_mode)
                    else stat.S_IFREG | 0o640
                )
        self.add("tools/runtime_guard.py", 0o640, uid=1200, gid=10001)
        self.check()

    def test_owner_class_does_not_fall_back_to_group_or_other(self):
        for name, mode in (
            ("config/platform/settings.json", 0o040),
            ("contracts/text-dialogue/v1/manifest.json", 0o044),
            ("tools/runtime_guard.py", 0o044),
        ):
            with self.subTest(name=name):
                original = self.metadata[name]
                self.add(name, mode)
                with self.assertRaisesRegex(Refused, "runtime_mount_access_missing"):
                    self.check()
                self.metadata[name] = original

    def test_zero_mode_settings_rejected_for_every_product(self):
        for product in PRODUCTS:
            with self.subTest(product=product):
                name = f"config/{product}/settings.json"
                self.add(name, 0o000)
                with self.assertRaisesRegex(Refused, "runtime_mount_access_missing"):
                    self.check()
                self.add(name, 0o640)

    def test_config_tls_and_contract_directories_require_traversal(self):
        for name in (
            "config/platform",
            "config/memory/tls",
            "contracts",
            "contracts/text-dialogue/v1",
        ):
            with self.subTest(name=name):
                original = self.metadata[name]
                self.add(name, 0o640, directory=True)
                with self.assertRaisesRegex(Refused, "runtime_mount_access_missing"):
                    self.check()
                self.metadata[name] = original

    def test_foreign_owner_contract_without_runtime_read_access_rejected(self):
        for name, directory in (
            ("contracts", True),
            ("contracts/text-dialogue/v1/manifest.json", False),
        ):
            with self.subTest(name=name):
                original = self.metadata[name]
                self.add(
                    name,
                    0o750 if directory else 0o640,
                    uid=1200,
                    gid=1200,
                    directory=directory,
                )
                with self.assertRaisesRegex(Refused, "runtime_mount_access_missing"):
                    self.check()
                self.metadata[name] = original

    def test_direct_guard_bind_requires_read_but_not_host_parent_access(self):
        # These ancestors are not mounted. Container checks must not inspect their modes.
        self.add("tools", 0o000, uid=1200, gid=1200, directory=True)
        self.add("config", 0o000, uid=1200, gid=1200, directory=True)
        self.check()
        self.assertFalse({"tools", "config"} & self.visited)
        for mode in (0o000, 0o600):
            with self.subTest(mode=oct(mode)):
                self.add("tools/runtime_guard.py", mode, uid=1200, gid=1200)
                with self.assertRaisesRegex(Refused, "runtime_mount_access_missing"):
                    self.check()

    def test_writable_mount_roots_require_write_and_traverse(self):
        for category in ("data", "logs"):
            for mode in (0o500, 0o600):
                with self.subTest(category=category, mode=oct(mode)):
                    self.add(f"{category}/platform", mode, directory=True)
                    with self.assertRaisesRegex(
                        Refused, "runtime_owner_permissions_missing"
                    ):
                        self.check()
                    self.add(f"{category}/platform", 0o750, directory=True)

    def test_private_materials_do_not_gain_world_or_unrelated_group_access(self):
        name = "config/platform/tls/server.key"
        for mode, gid in ((0o644, 10001), (0o660, 10001), (0o640, 1200)):
            with self.subTest(mode=oct(mode), gid=gid):
                self.add(name, mode, gid=gid)
                with self.assertRaisesRegex(Refused, "runtime_config_permissions"):
                    self.check()
        self.add(
            name, 0o600, gid=1200
        )  # Owner-only is valid; unrelated group gets nothing.
        self.check()
        for mode in (0o000, 0o640, 0o644):
            with self.subTest(env_mode=oct(mode)):
                self.add("private/platform.env", mode, uid=1200, gid=1200)
                with self.assertRaisesRegex(Refused, "private_env_permissions"):
                    self.check()

    def test_public_inputs_must_not_be_writable_by_other_groups(self):
        for name in (
            "contracts/text-dialogue/v1/manifest.json",
            "tools/runtime_guard.py",
        ):
            with self.subTest(name=name):
                self.add(name, 0o666, uid=1200, gid=1200)
                with self.assertRaisesRegex(Refused, "runtime_public_input_writable"):
                    self.check()
                self.add(name, 0o644, uid=1200, gid=1200)


if __name__ == "__main__":
    unittest.main()
