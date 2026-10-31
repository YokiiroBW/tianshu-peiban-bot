"""Restore never accepts a shared database or an escaping file payload."""

import importlib.util
import json
import os
from pathlib import Path
import tarfile

import pytest


def backup_module(monkeypatch):
    directory = Path(__file__).parents[2] / "deploy/core"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location("core_backup", directory / "backup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://tianshu_core:unused@localhost/xinghai",
        "postgresql://postgres:unused@localhost/tianshu_core",
        "dbname=tianshu_core user=tianshu_core hostaddr=127.0.0.1",
    ],
)
def test_backup_rejects_shared_database_identity_before_connecting(monkeypatch, dsn):
    module = backup_module(monkeypatch)
    with pytest.raises(ValueError, match="dedicated_core_database_required"):
        module.database_parameters(dsn)


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../elsewhere", tarfile.REGTYPE),
        ("/etc/passwd", tarfile.REGTYPE),
        ("data/escape", tarfile.SYMTYPE),
        ("data/hardlink", tarfile.LNKTYPE),
    ],
)
def test_restore_rejects_escape_and_link_members(monkeypatch, tmp_path, name, kind):
    module = backup_module(monkeypatch)
    path = tmp_path / "malformed.tar"
    with tarfile.open(path, "w") as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = "../../elsewhere" if kind in {tarfile.SYMTYPE, tarfile.LNKTYPE} else ""
        archive.addfile(member)
    with tarfile.open(path) as archive:
        with pytest.raises(ValueError, match="unsafe_backup_payload"):
            module.validate_members(archive)


def test_compose_manifest_rejects_paths_outside_the_installation(monkeypatch, tmp_path):
    module = backup_module(monkeypatch)
    root = tmp_path / "installation"
    root.mkdir()
    with pytest.raises(ValueError, match="unsafe_installation_compose_file"):
        module.configuration_files(root, {"config_files": ["../foreign.json"]})


@pytest.mark.skipif(os.name != "posix", reason="Linux deployment symlink boundary")
def test_compose_manifest_rejects_a_symlinked_parent(monkeypatch, tmp_path):
    module = backup_module(monkeypatch)
    root, outside = tmp_path / "installation", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "media.json").write_text("{}")
    (root / "overrides").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe_installation_compose_file"):
        module.configuration_files(root, {"config_files": ["overrides/media.json"]})


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() != 0, reason="actual Linux root file restore"
)
def test_multifile_compose_backup_restores_the_exact_reviewed_order(monkeypatch, tmp_path):
    module = backup_module(monkeypatch)
    original = tmp_path / "installation"
    original.mkdir()
    for name in ("config", "data", "overrides"):
        (original / name).mkdir()
    for name, content in {
        "compose.yaml": "services: {}\n",
        "overrides/media.json": '{"services":{"tianshu":{"ports":["127.0.0.1:29999:18446"]}}}',
        "seccomp-chromium.json": "{}",
        "data/artifact": "actual saved bytes",
    }.items():
        (original / name).write_text(content)
    dsn = "postgresql://tianshu_core:synthetic@localhost:59999/tianshu_core"
    values = {"TIANSHU_DATABASE_URL": dsn, "TIANSHU_MASTER_KEY": "synthetic-sole-anchor"}
    (original / ".env").write_text(
        "".join(key + "=" + json.dumps(value) + "\n" for key, value in values.items())
    )
    install = {
        "installation_id": "fixture",
        "deployment_root": str(original),
        "source_sha": "0" * 40,
        "external_binds": [],
        "config_files": [str(original / "compose.yaml"), "overrides/media.json"],
        "database": {"host": "localhost", "port": 59999},
    }
    (original / "installation.json").write_text(json.dumps(install))
    monkeypatch.setattr(
        module, "require_stopped", lambda root: {"Image": "fixture", "Id": "fixture"}
    )
    calls = []

    def pg_tool(binary, arguments, target, temporary):
        calls.append(binary)
        assert target == dsn
        if binary == "pg_dump":
            Path(arguments[arguments.index("--file") + 1]).write_bytes(b"synthetic database dump")
        elif binary == "pg_restore":
            assert arguments == [
                "--dbname",
                "tianshu_core",
                "--single-transaction",
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                str(tmp_path / "backup/database.dump"),
            ]
            assert all("synthetic" not in argument for argument in arguments)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, statement):
            self.statement = statement
            return self

        def fetchone(self):
            return ("0.8.2",) if "extversion" in self.statement else None

        def fetchall(self):
            return [("0001",)] if "schema_migrations" in self.statement else [("unknown", 1)]

    monkeypatch.setattr(module, "pg_tool", pg_tool)
    monkeypatch.setattr(module.psycopg, "connect", lambda *args, **kwargs: Connection())
    bundle = tmp_path / "backup"
    result = module.backup(original, bundle)
    dsn_file = tmp_path / "new.dsn"
    dsn_file.write_text(dsn)
    restored = tmp_path / "restored"
    module.restore(bundle, restored, dsn_file, result["manifest_sha256"])
    assert calls == ["pg_dump", "pg_restore"]
    installation = json.loads((restored / "installation.json").read_text())
    assert installation["config_files"] == ["compose.yaml", "overrides/media.json"]
    assert module.configuration_files(restored, installation) == (
        "compose.yaml",
        "overrides/media.json",
    )
    assert (restored / "overrides/media.json").read_bytes() == (
        original / "overrides/media.json"
    ).read_bytes()
    assert (
        module.environment(restored / ".env")["TIANSHU_MASTER_KEY"] == values["TIANSHU_MASTER_KEY"]
    )
    with tarfile.open(bundle / "payload.tar") as archive:
        missing = tmp_path / "missing.tar"
        with tarfile.open(missing, "w") as output:
            for member in archive.getmembers():
                if member.name != "overrides/media.json":
                    output.addfile(member, archive.extractfile(member) if member.isfile() else None)
    with tarfile.open(missing) as archive:
        with pytest.raises(ValueError, match="unsafe_backup_payload"):
            module.validate_members(archive)
