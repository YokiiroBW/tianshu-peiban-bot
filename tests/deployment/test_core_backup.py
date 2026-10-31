"""Restore never accepts a shared database or an escaping file payload."""

import importlib.util
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
