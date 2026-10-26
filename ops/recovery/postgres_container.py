"""Stream native PG backup clients through one pinned local Docker container.

This module also runs as a standalone file from a private wrapper. It uses only
the standard library and never puts credentials in Docker's command arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


TOOLS = {"psql", "pg_dump", "pg_restore"}
PG_ENVIRONMENT = {
    "PGHOST",
    "PGHOSTADDR",
    "PGPORT",
    "PGUSER",
    "PGPASSWORD",
    "PGDATABASE",
    "PGCONNECT_TIMEOUT",
    "PGOPTIONS",
    "PGAPPNAME",
    "PGCLIENTENCODING",
    "PGTZ",
    "PGDATESTYLE",
    "PGSSLMODE",
    "PGSSLCERT",
    "PGSSLKEY",
    "PGSSLROOTCERT",
    "PGSSLCRL",
    "PGSSLCRLDIR",
    "PGSSLSNI",
    "PGCHANNELBINDING",
    "PGGSSENCMODE",
    "PGKRBSRVNAME",
    "PGREQUIREAUTH",
    "PGLOADBALANCEHOSTS",
    "PGSSLNEGOTIATION",
    "PGTARGETSESSIONATTRS",
    "PGPASSFILE",
}
INSPECT_FORMAT = (
    '{"id":{{json .Id}},"image_id":{{json .Image}},'
    '"running":{{json .State.Running}},"paused":{{json .State.Paused}},'
    '"restarting":{{json .State.Restarting}},"ports":{{json .NetworkSettings.Ports}}}'
)
FLAGS = {
    "psql": {"-X", "--no-password", "--no-align", "--tuples-only"},
    "pg_dump": {"--no-password", "--strict-names"},
    "pg_restore": {
        "--no-password",
        "--no-owner",
        "--no-privileges",
        "--single-transaction",
        "--exit-on-error",
    },
}
OPTIONS = {
    "psql": {"--command", "-c", "--set", "-v"},
    "pg_dump": {"--format", "-F", "--schema", "-n", "--file", "-f"},
    "pg_restore": {"--dbname", "-d"},
}


class BridgeError(Exception):
    """Only fixed codes may leave this CLI."""


def require(condition, code):
    if not condition:
        raise BridgeError(code)


def load_configuration(path):
    try:
        path = Path(path)
        info = path.lstat()
        require(
            stat.S_ISREG(info.st_mode), "postgresql_container_configuration_invalid"
        )
        if os.name == "posix":
            require(
                info.st_uid == os.geteuid() and not info.st_mode & 0o077,
                "postgresql_container_configuration_private_required",
            )
        require(info.st_size <= 65536, "postgresql_container_configuration_invalid")
        value = json.loads(path.read_bytes())
        require(isinstance(value, dict), "postgresql_container_configuration_invalid")
        return value
    except (OSError, ValueError):
        raise BridgeError("postgresql_container_configuration_invalid") from None


def container_configuration(document):
    value = document.get("postgres_container")
    require(
        isinstance(value, dict)
        and set(value) == {"docker", "container_id", "image_id", "endpoint"},
        "postgresql_container_configuration_invalid",
    )
    require(
        isinstance(value["docker"], str) and Path(value["docker"]).is_absolute(),
        "postgresql_container_configuration_invalid",
    )
    require(
        isinstance(value["container_id"], str)
        and re.fullmatch(r"[a-f0-9]{64}", value["container_id"]),
        "postgresql_container_configuration_invalid",
    )
    require(
        isinstance(value["image_id"], str)
        and re.fullmatch(r"sha256:[a-f0-9]{64}", value["image_id"]),
        "postgresql_container_configuration_invalid",
    )
    endpoint = value["endpoint"]
    require(
        isinstance(endpoint, dict)
        and set(endpoint) == {"host", "port", "container_port"},
        "postgresql_container_configuration_invalid",
    )
    require(
        isinstance(endpoint["host"], str)
        and bool(endpoint["host"])
        and all(ord(ch) > 32 for ch in endpoint["host"])
        and all(
            type(endpoint[key]) is int and 0 < endpoint[key] <= 65535
            for key in ("port", "container_port")
        ),
        "postgresql_container_configuration_invalid",
    )
    return value


def client_environment(configuration, environment):
    endpoint = configuration["endpoint"]
    require(
        environment.get("PGHOST") == endpoint["host"]
        and environment.get("PGPORT") == str(endpoint["port"])
        and environment.get("PGHOSTADDR", "") in {"", endpoint["host"]},
        "postgresql_container_endpoint_mismatch",
    )
    require(
        all(
            isinstance(environment.get(key), str)
            and bool(environment[key])
            and "\x00" not in environment[key]
            for key in ("PGUSER", "PGDATABASE")
        ),
        "postgresql_container_connection_required",
    )
    require(
        not any(part in environment["PGDATABASE"] for part in ("=", "://", "\n", "\r")),
        "postgresql_container_connection_required",
    )
    result = {
        key: value for key, value in environment.items() if not key.startswith("PG")
    }
    for key in PG_ENVIRONMENT:
        if key in environment:
            require(
                isinstance(environment[key], str) and "\x00" not in environment[key],
                "postgresql_container_environment_invalid",
            )
            result[key] = environment[key]
    return result


def client_arguments(tool, arguments, environment):
    """Accept the existing native backup argv, with connections fixed by PG env."""
    require(tool in TOOLS, "postgresql_container_tool_invalid")
    result, files = [], []
    output = None
    custom_format = False
    database = None
    iterator = iter(arguments)
    for argument in iterator:
        require(
            isinstance(argument, str) and "\x00" not in argument,
            "postgresql_container_arguments_invalid",
        )
        option, equal, value = argument.partition("=")
        if argument in FLAGS[tool]:
            result.append(argument)
        elif option in OPTIONS[tool]:
            if not equal:
                value = next(iterator, None)
            require(
                isinstance(value, str) and bool(value) and "\x00" not in value,
                "postgresql_container_arguments_invalid",
            )
            if tool == "pg_dump" and option in {"--file", "-f"}:
                require(output is None, "postgresql_container_arguments_invalid")
                output = Path(value)
            elif tool == "pg_restore":
                require(
                    database is None
                    and value == environment["PGDATABASE"]
                    and re.fullmatch(r"ts_restore_[a-f0-9]{32}", value),
                    "postgresql_container_restore_target_invalid",
                )
                database = value
                result.extend(["--dbname", value])
            else:
                if tool == "pg_dump" and option in {"--format", "-F"}:
                    require(
                        value in {"c", "custom"},
                        "postgresql_container_arguments_invalid",
                    )
                    custom_format = True
                result.extend([option, value])
        elif tool == "pg_restore" and not argument.startswith("-"):
            files.append(Path(argument))
        else:
            raise BridgeError("postgresql_container_arguments_invalid")
    if tool == "pg_dump":
        require(
            output is not None and output.is_absolute() and custom_format,
            "postgresql_container_dump_file_required",
        )
    if tool == "pg_restore":
        require(
            database is not None and len(files) == 1 and files[0].is_absolute(),
            "postgresql_container_restore_file_required",
        )
    return result, output, files[0] if files else None


def verify_container(configuration, environment, runner):
    try:
        result = runner(
            [
                configuration["docker"],
                "inspect",
                "--type",
                "container",
                "--format",
                INSPECT_FORMAT,
                configuration["container_id"],
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        raise BridgeError("postgresql_container_identity_unavailable") from None
    require(result.returncode == 0, "postgresql_container_identity_unavailable")
    try:
        current = json.loads(result.stdout)
        endpoint = configuration["endpoint"]
        bindings = current["ports"].get(str(endpoint["container_port"]) + "/tcp") or []
        valid = (
            current["id"] == configuration["container_id"]
            and current["image_id"] == configuration["image_id"]
        )
        valid = (
            valid
            and current["running"] is True
            and current["paused"] is False
            and current["restarting"] is False
        )
        valid = valid and any(
            binding.get("HostPort") == str(endpoint["port"])
            and binding.get("HostIp") in {endpoint["host"], "0.0.0.0", "::"}
            for binding in bindings
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        valid = False
    require(valid, "postgresql_container_identity_mismatch")


def file_identity(info):
    identity = (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
    )
    # Windows exposes creation time here, with different path/handle refresh
    # timing. POSIX ctime also detects metadata changes during a restore read.
    return identity + ((info.st_ctime_ns,) if os.name == "posix" else ())


def stream_client(command, environment, inputs, outputs):
    """Relay through an internal pipe so only this wrapper owns caller stdout.

    Killing the wrapper at the existing fingerprint deadline then closes caller
    stdout immediately, while native client errors still become a fixed code.
    """
    deadline = time.monotonic() + 600
    with subprocess.Popen(
        command,
        env=environment,
        stdin=inputs,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as process:

        def copy_output():
            while chunk := process.stdout.read1(65536):
                outputs.write(chunk)
                outputs.flush()

        executor = ThreadPoolExecutor(max_workers=1)
        pending = executor.submit(copy_output)
        try:
            pending.result(timeout=max(1, deadline - time.monotonic()))
            require(
                process.wait(timeout=max(1, deadline - time.monotonic())) == 0,
                "postgresql_container_client_failed",
            )
        except BaseException:
            if process.poll() is None:
                process.kill()
            process.wait()
            raise
        finally:
            executor.shutdown(wait=True)


def execute(
    document,
    tool,
    arguments,
    *,
    environment=None,
    input_stream=None,
    output_stream=None,
    runner=None,
    streaming_psql=False,
):
    configuration = container_configuration(document)
    environment = client_environment(
        configuration, os.environ if environment is None else environment
    )
    arguments, output, archive = client_arguments(tool, arguments, environment)
    runner = subprocess.run if runner is None else runner
    verify_container(configuration, environment, runner)
    command = [configuration["docker"], "exec", "-i"]
    for key in sorted(PG_ENVIRONMENT & environment.keys()):
        command.extend(["-e", key])
    command.extend([configuration["container_id"], tool, *arguments])
    inputs = sys.stdin.buffer if input_stream is None else input_stream
    outputs = sys.stdout.buffer if output_stream is None else output_stream
    source = destination = None
    try:
        if archive is not None:
            info = archive.lstat()
            require(
                stat.S_ISREG(info.st_mode), "postgresql_container_restore_file_invalid"
            )
            descriptor = os.open(archive, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            source = os.fdopen(descriptor, "rb")
            require(
                file_identity(os.fstat(source.fileno())) == file_identity(info),
                "postgresql_container_restore_file_changed",
            )
            inputs = source
        if output is not None:
            descriptor = os.open(
                output,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            destination = os.fdopen(descriptor, "wb")
            outputs = destination
        if tool == "psql" and streaming_psql:
            stream_client(command, environment, inputs, outputs)
        else:
            result = runner(
                command,
                env=environment,
                stdin=inputs,
                stdout=outputs,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=600,
            )
            require(result.returncode == 0, "postgresql_container_client_failed")
        if destination is not None:
            destination.flush()
            os.fsync(destination.fileno())
        if source is not None:
            require(
                file_identity(os.fstat(source.fileno())) == file_identity(info)
                and file_identity(archive.lstat()) == file_identity(info),
                "postgresql_container_restore_file_changed",
            )
        return 0
    except (OSError, subprocess.SubprocessError):
        raise BridgeError("postgresql_container_client_failed") from None
    finally:
        if source is not None:
            source.close()
        if destination is not None:
            destination.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--tool", required=True, choices=sorted(TOOLS))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    command = arguments.arguments
    if command[:1] == ["--"]:
        command = command[1:]
    try:
        return execute(
            load_configuration(arguments.config),
            arguments.tool,
            command,
            streaming_psql=True,
        )
    except BridgeError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        print("postgresql_container_client_failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
