"""Cross-platform ordering of sealed A1 file trees."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from ops.recovery import a1_once, a1_prepare
from ops.recovery.safety import canonical, file_hash


def put(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def digest(rows):
    return hashlib.sha256(canonical(rows)).hexdigest()


class PathOrder(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_code_tree_uses_case_sensitive_posix_bytes_on_both_hosts(self):
        paths = [put(self.root, "ops/recovery/a1_once.py", "driver\n"),
                 put(self.root, "ops/Zeta.txt", "upper\n"),
                 put(self.root, "ops/alpha.txt", "lower\n"),
                 put(self.root, "deploy/Zeta/item.txt", "deploy upper\n"),
                 put(self.root, "deploy/alpha/item.txt", "deploy lower\n")]
        relative = lambda path: path.relative_to(self.root).as_posix()
        expected = []
        windows_order = []
        for base in ("ops", "deploy"):
            subset = [path for path in paths if path.relative_to(self.root).parts[0] == base]
            expected.extend((relative(path), file_hash(path)) for path in
                            sorted(subset, key=lambda path: relative(path).encode("utf-8")))
            windows_order.extend((relative(path), file_hash(path)) for path in
                                 sorted(subset, key=lambda path: relative(path).casefold()))
        self.assertNotEqual(expected, windows_order)
        self.assertEqual(expected, a1_once._code_files(self.root))
        original = a1_once._code_tree(self.root)
        self.assertEqual(digest(expected), original)
        self.assertNotEqual(digest(windows_order), original)

        changed = paths[1]
        changed.write_text("changed\n", encoding="utf-8")
        self.assertNotEqual(original, a1_once._code_tree(self.root))
        changed.write_text("upper\n", encoding="utf-8")
        self.assertEqual(original, a1_once._code_tree(self.root))
        added = put(self.root, "ops/new.txt", "new\n")
        self.assertNotEqual(original, a1_once._code_tree(self.root))
        added.unlink()
        self.assertEqual(original, a1_once._code_tree(self.root))
        paths[2].rename(self.root / "ops/renamed.txt")
        self.assertNotEqual(original, a1_once._code_tree(self.root))

    def test_repository_tree_uses_same_case_sensitive_order(self):
        paths = [put(self.root, "Zeta/item.txt", "upper\n"),
                 put(self.root, "alpha/item.txt", "lower\n")]
        relative = lambda path: path.relative_to(self.root).as_posix()
        expected = [(relative(path), file_hash(path)) for path in
                    sorted(paths, key=lambda path: relative(path).encode("utf-8"))]
        windows_order = [(relative(path), file_hash(path)) for path in
                         sorted(paths, key=lambda path: relative(path).casefold())]
        original = a1_prepare._tree(self.root)
        self.assertEqual(digest(expected), original)
        self.assertNotEqual(digest(windows_order), original)
        paths[0].write_text("changed\n", encoding="utf-8")
        self.assertNotEqual(original, a1_prepare._tree(self.root))
        paths[0].write_text("upper\n", encoding="utf-8")
        put(self.root, "new.txt", "new\n")
        self.assertNotEqual(original, a1_prepare._tree(self.root))


if __name__ == "__main__":
    unittest.main()
