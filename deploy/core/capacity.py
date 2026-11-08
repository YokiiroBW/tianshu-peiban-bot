"""Exact-owner, fail-closed capacity protection for the new single product deployment."""

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import stat
import time


class GuardError(ValueError):
    pass


def inside(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def overlap(left, right):
    return inside(left, right) or inside(right, left)


def docker(binary, *arguments, timeout=15):
    result = subprocess.run(
        [binary, *arguments], capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise GuardError("docker_query_failed")
    return result.stdout


class TreeMeter:
    """Incremental allocated-byte accounting; never rescan every NAS inode on every poll."""

    def __init__(self, roots):
        unique = sorted({Path(root).resolve() for root in roots}, key=lambda path: len(path.parts))
        self.roots = tuple(
            path
            for index, path in enumerate(unique)
            if not any(path.is_relative_to(parent) for parent in unique[:index])
        )
        self.iterator = None
        self.current = 0
        self.last = None
        self.completed_at = None

    def walk(self):
        pending = [iter(self.roots)]
        seen = set()
        try:
            while pending:
                try:
                    entry = next(pending[-1])
                except StopIteration:
                    scanner = pending.pop()
                    if hasattr(scanner, "close"):
                        scanner.close()
                    continue
                path = Path(entry.path) if isinstance(entry, os.DirEntry) else entry
                try:
                    metadata = (
                        entry.stat(follow_symlinks=False)
                        if isinstance(entry, os.DirEntry)
                        else path.lstat()
                    )
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink > 1:
                    key = (metadata.st_dev, metadata.st_ino)
                    if key in seen:
                        continue
                    seen.add(key)
                # Yield before the next readdir: one enormous flat directory is bounded too.
                yield getattr(metadata, "st_blocks", (metadata.st_size + 511) // 512) * 512
                if stat.S_ISDIR(metadata.st_mode):
                    try:
                        pending.append(os.scandir(path))
                    except FileNotFoundError:
                        pass
        finally:
            for scanner in pending:
                if hasattr(scanner, "close"):
                    scanner.close()

    def step(self, *, seconds=0.25, entries=5000):
        if self.iterator is None:
            self.iterator = self.walk()
            self.current = 0
        deadline = time.monotonic() + seconds
        for _ in range(entries):
            try:
                self.current += next(self.iterator)
            except StopIteration:
                self.last = self.current
                self.completed_at = datetime.now(UTC).isoformat()
                self.iterator = None
                break
            if time.monotonic() >= deadline:
                break
        return {
            "lower_bound_bytes": self.current,
            "last_complete_bytes": self.last,
            "complete": self.iterator is None,
            "completed_at": self.completed_at,
        }


class CapacityGuard:
    def __init__(self, config):
        self.config = config
        if config.get("schema_version") != "tianshu.capacity.v1":
            raise GuardError("capacity_schema_required")
        raw_root = Path(config["deployment_root"])
        self.root = raw_root.resolve()
        if not raw_root.is_absolute() or self.root == Path(self.root.anchor):
            raise GuardError("dedicated_deployment_root_required")
        external = config.get("owned_external_paths", [])
        if any(
            not Path(path).is_absolute() or Path(path).resolve() == Path(Path(path).anchor)
            for path in external
        ):
            raise GuardError("dedicated_owned_external_path_required")
        self.protected = [self.root, *(Path(path).resolve() for path in external)]
        targets = config["targets"]
        if not targets or any(
            not Path(target).is_absolute()
            or not any(inside(target, root) for root in self.protected)
            for target in targets
        ):
            raise GuardError("capacity_target_outside_owned_root")
        self.meter = TreeMeter(targets)
        self.cache = {}
        self.verified = None
        self.binary = config.get("docker_binary", "/usr/bin/docker")
        self.expected_mounts = sorted(
            (str(Path(item["source"]).resolve()), item["target"], bool(item["read_only"]))
            for item in config["container"]["binds"]
        )
        self.state = Path(config["state_directory"]).resolve() / "state.json"
        self.state.parent.mkdir(parents=True, exist_ok=True)

    def containers(self):
        identifiers = docker(self.binary, "ps", "-aq", "--no-trunc").split()
        missing = [identifier for identifier in identifiers if identifier not in self.cache]
        if missing:
            rows = json.loads(docker(self.binary, "inspect", *missing))
            for row in rows:
                for mount in row.get("Mounts", []):
                    if mount["Type"] == "bind":
                        mount["Source"] = str(Path(mount["Source"]).resolve())
                # Retain only immutable ownership/mount metadata, never Config.Env or secrets.
                self.cache[row["Id"]] = {
                    "Id": row["Id"],
                    "Image": row["Image"],
                    "Mounts": row.get("Mounts", []),
                    "Config": {
                        key: row["Config"].get(key)
                        for key in ("Labels", "Entrypoint", "Cmd", "User")
                    },
                    "HostConfig": {
                        key: row["HostConfig"].get(key)
                        for key in ("ReadonlyRootfs", "Privileged", "PidMode")
                    },
                }
        self.cache = {
            identifier: self.cache[identifier]
            for identifier in identifiers
            if identifier in self.cache
        }
        return list(self.cache.values())

    def verify(self, row):
        expected = self.config["container"]
        labels = row["Config"]["Labels"] or {}
        if row["Id"] != expected["id"] or row["Image"] != expected["image_id"]:
            raise GuardError("foreign_deployment_identity")
        required = {
            "com.docker.compose.project": expected["project"],
            "com.docker.compose.service": expected["service"],
            "com.docker.compose.project.working_dir": str(self.root),
            "com.docker.compose.project.config_files": expected.get(
                "config_files", str(self.root / "compose.yaml")
            ),
            "io.tianshu.product": "core",
            "io.tianshu.release": expected["release_id"],
        }
        if any(labels.get(key) != value for key, value in required.items()):
            raise GuardError("foreign_deployment_labels")
        if (
            row["Config"]["Entrypoint"] != expected["entrypoint"]
            or row["Config"]["Cmd"] != expected["command"]
            or row["Config"]["User"] != expected["user"]
        ):
            raise GuardError("foreign_deployment_command")
        if (
            not row["HostConfig"]["ReadonlyRootfs"]
            or row["HostConfig"]["Privileged"]
            or row["HostConfig"]["PidMode"] == "host"
        ):
            raise GuardError("foreign_deployment_isolation")
        actual = [
            {
                "source": mount["Source"],
                "target": mount["Destination"],
                "read_only": not mount["RW"],
            }
            for mount in row["Mounts"]
            if mount["Type"] == "bind"
        ]
        mounts = sorted((entry["source"], entry["target"], entry["read_only"]) for entry in actual)
        if mounts != self.expected_mounts or any(
            mount["Type"] not in {"bind", "tmpfs"} for mount in row["Mounts"]
        ):
            raise GuardError("foreign_deployment_mount")

    def owned_container(self):
        rows = self.containers()
        target = next((row for row in rows if row["Id"] == self.config["container"]["id"]), None)
        self.verified = None
        if target is None:
            raise GuardError("deployment_container_missing")
        self.verify(target)
        self.verified = target["Id"]
        for row in rows:
            if row["Id"] == target["Id"]:
                continue
            if any(
                mount.get("Source")
                and any(
                    Path(mount["Source"]).is_relative_to(root)
                    or root.is_relative_to(mount["Source"])
                    for root in self.protected
                )
                for mount in row["Mounts"]
                if mount["Type"] == "bind"
            ):
                raise GuardError("foreign_deployment_mount")
        return target

    def inspect(self):
        target = self.owned_container()
        free = {path: shutil.disk_usage(path).free for path in self.config["free_paths"]}
        size = self.meter.step()
        image_bytes = self.config["container"]["image_bytes"]
        report = {
            "schema_version": "tianshu.capacity.state.v1",
            "observed_at": datetime.now(UTC).isoformat(),
            "container_id": target["Id"],
            "free_bytes": free,
            "deployment": size,
            "state": "healthy",
        }
        if any(value < self.config["min_free_bytes"] for value in free.values()):
            raise GuardError("minimum_free_bytes_breached")
        report["deployment"]["image_bytes"] = image_bytes
        # The deployment tree includes mutable business data, logs and media
        # staging.  Its size is useful telemetry, but is not a product quota.
        # Disk exhaustion is governed separately by the actual free-space check.
        return report

    def write(self, report):
        temporary = self.state.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(report, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        temporary.replace(self.state)

    def stop_owned(self):
        if not self.verified:
            return False
        # Repeat exact immutable ownership immediately before the two targeted writes.
        fresh = json.loads(docker(self.binary, "inspect", self.verified))[0]
        for mount in fresh.get("Mounts", []):
            if mount["Type"] == "bind":
                mount["Source"] = str(Path(mount["Source"]).resolve())
        self.verify(fresh)
        docker(self.binary, "update", "--restart=no", self.verified)
        docker(
            self.binary,
            "stop",
            "--time",
            str(self.config["term_timeout_seconds"]),
            self.verified,
            timeout=self.config["term_timeout_seconds"] + 15,
        )
        return True

    def poll(self, *, enforce=False):
        previous, broken = {}, False
        try:
            previous = json.loads(self.state.read_text()) if self.state.exists() else {}
        except (OSError, ValueError):
            broken = True
        try:
            report = self.inspect()
            if broken:
                raise GuardError("capacity_state_unreadable")
            if previous.get("latched"):
                report.update(state="held", latched=True, reason=previous.get("reason"))
                if enforce:
                    report["stopped"] = self.stop_owned()
        except (GuardError, OSError, ValueError, subprocess.TimeoutExpired) as error:
            reason = str(error) if isinstance(error, GuardError) else type(error).__name__
            stopped, stop_error = False, None
            if enforce and self.verified:
                try:
                    stopped = self.stop_owned()
                except (GuardError, OSError, ValueError, subprocess.TimeoutExpired) as failure:
                    stop_error = type(failure).__name__
            report = {
                "schema_version": "tianshu.capacity.state.v1",
                "observed_at": datetime.now(UTC).isoformat(),
                "state": "blocked",
                "reason": reason,
                "latched": bool(enforce or previous.get("latched")),
                "stopped": stopped,
                "stop_error": stop_error,
            }
        self.write(report)
        return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args()
    guard = CapacityGuard(json.loads(Path(args.config).read_text()))
    if args.clear:
        report = guard.inspect()
        while not report["deployment"]["complete"]:
            time.sleep(0.05)
            report = guard.inspect()
        guard.write(report)
        return
    while True:
        report = guard.poll(enforce=args.watch)
        if not args.watch:
            print(json.dumps(report))
            raise SystemExit(0 if report["state"] == "healthy" else 1)
        time.sleep(guard.config["poll_seconds"])


if __name__ == "__main__":
    main()
