"""A new installation contains usable local capability paths and matching helper identities."""

import importlib.util
import json
import os
from pathlib import Path
import stat

import pytest
import yaml

from tianshu.capabilities.media.runtime import validate_media
from tianshu.capabilities.media.comics.plugin import configuration as comic_file_configuration


def test_chromium_sandbox_profile_allows_namespace_chroot_without_host_capability():
    directory = Path(__file__).parents[2] / "deploy/core"
    profile = json.loads((directory / "seccomp-chromium.json").read_text())
    service = yaml.safe_load((directory / "compose.yaml").read_text())["services"]["tianshu"]
    settings = json.loads((directory / "settings.example.json").read_text())
    rules = [rule for rule in profile["syscalls"] if "chroot" in rule["names"]]
    assert len(rules) == 1
    assert rules[0]["action"] == "SCMP_ACT_ALLOW" and rules[0]["args"] == []
    assert rules[0]["includes"] == rules[0]["excludes"] == {}
    allowed = {name for rule in profile["syscalls"]
        if rule["action"] == "SCMP_ACT_ALLOW" and not rule.get("args")
        and not rule.get("includes") and not rule.get("excludes") for name in rule["names"]}
    assert {"clone", "setns", "unshare", "chroot"} <= allowed
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    caps = {"CAP_" + name.removeprefix("CAP_") for name in service["cap_add"]}
    assert service["cap_drop"] == ["ALL"]
    assert not ({"CAP_SYS_ADMIN", "CAP_SYS_CHROOT"} & caps)
    assert not service.get("privileged", False)
    assert set(service["security_opt"]) == {
        "no-new-privileges:true", "seccomp=./seccomp-chromium.json"
    }
    assert service["user"] == "0:10001"  # Directory initializer; business children drop UID.
    assert settings["supervisor"]["uid"] == settings["supervisor"]["gid"] == 10001
    assert settings["capabilities"]["content"]["browser"]["chromium_sandbox"] is True


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() != 0, reason="actual Linux bootstrap ownership"
)
def test_bootstrap_provisions_native_media_projects_and_fixed_comic_helpers(tmp_path):
    directory = Path(__file__).parents[2] / "deploy/core"
    spec = importlib.util.spec_from_file_location("core_bootstrap", directory / "bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    password = tmp_path / "database-password"
    password.write_text("synthetic-unconnected-password")
    root = tmp_path / "new-installation"
    module.bootstrap(
        root,
        origin="http://example.invalid:18446",
        mode="http",
        image="sha256:" + "0" * 64,
        source_sha="0" * 40,
        database_host="database.example.invalid",
        database_password_file=password,
    )
    settings = json.loads((root / "config/settings.json").read_text())
    capabilities = settings["capabilities"]
    media = validate_media(
        {**capabilities, "database_path": str(root / "data/capabilities/library")}
    )
    assert media is not None and media["targets"] == {}
    assert media["engine"]["python"] == "/opt/tianshu/.venv/bin/python"
    assert media["engine"]["ffmpeg"] == "/usr/bin/ffmpeg"
    assert media["engine"]["ffprobe"] == "/usr/bin/ffprobe"
    assert capabilities["projects"]["allowed_roots"] == ["/data/projects"]
    assert "books" not in capabilities and "music" not in capabilities
    plugin = comic_file_configuration(capabilities)
    supervisor = settings["supervisor"]
    assert plugin["runtime_socket"] == supervisor["comics"]["socket"]

    local_directories = {
        "/data/projects",
        media["staging_directory"],
        plugin["defaults"]["import_directory"],
        "/data/media",
        "/data/media/books",
        "/data/media/music",
    }
    for role in ("inputs", "publisher"):
        helper = supervisor["comics"][role]
        assert helper["uid"] == helper["gid"] == supervisor["uid"] == supervisor["gid"] == 10001
        path = root / "config" / Path(helper["settings"]).relative_to("/config")
        document = json.loads(path.read_text())
        assert document["client_uid"] == supervisor["uid"]
        assert document["socket"] == f"/run/tianshu/comics/{role}/service.sock"
        assert (
            document["socket"]
            == plugin["defaults"]["import_socket" if role == "inputs" else "publisher_socket"]
        )
        permissions = path.stat()
        assert (permissions.st_uid, permissions.st_gid, stat.S_IMODE(permissions.st_mode)) == (
            0,
            10001,
            0o640,
        )
        if role == "inputs":
            assert document["directory"] == plugin["defaults"]["import_directory"]
        else:
            assert set(document["targets"]) == {"comic-main", "comic-adult"}
            local_directories.update(document["targets"].values())

    for path in local_directories:
        directory = root / "data" / Path(path).relative_to("/data")
        assert directory.is_dir()
        permissions = directory.stat()
        assert (permissions.st_uid, permissions.st_gid, stat.S_IMODE(permissions.st_mode)) == (
            10001,
            10001,
            0o750,
        )
    permissions = (root / "config/comics").stat()
    assert (permissions.st_uid, permissions.st_gid, stat.S_IMODE(permissions.st_mode)) == (
        0,
        10001,
        0o750,
    )
    assert json.loads((root / "installation.json").read_text())["external_binds"] == []
    assert not (root / "BOOTSTRAP_INCOMPLETE").exists()
