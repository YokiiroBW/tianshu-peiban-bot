"""Read the existing NAS image tree through an owner-UID, read-only local process."""

import http.client
import json
import os
import socket
import uuid
from pathlib import Path

from ...contracts import Fault, require
from ..video_storage import sync_directory
from .packages import MAX_CHAPTER_BYTES

PROTOCOL = "comic-inputs-v1"
PREFIX = "/comics/v1/inputs"


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("comic-inputs", timeout=120)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class RemoteInputs:
    def __init__(self, socket_path, directory):
        self.socket, self.directory = socket_path, directory

    def root(self):
        require(self.directory, "comic_import_not_configured", 409)
        return Path(self.directory)

    def request(self, operation, body, *, destination=None, maximum=16 * 1024**2):
        connection = UnixConnection(self.socket)
        temporary = None
        try:
            connection.request(
                "POST",
                PREFIX + "/" + operation,
                json.dumps({"root": str(self.root()), **body}).encode(),
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            require(response.status != 403, "comic_input_service_forbidden", 409)
            if response.status != 200:
                raw = response.read(8193)
                require(len(raw) <= 8192, "comic_input_service_unavailable", 503)
                error = json.loads(raw)
                require(
                    False,
                    error.get("code", "comic_input_service_unavailable"),
                    409 if response.status < 500 else 503,
                )
            require(
                response.getheader("X-Comic-Protocol") == PROTOCOL,
                "comic_input_service_invalid_response",
                503,
            )
            expected = response.getheader("Content-Length")
            require(
                expected is not None and expected.isdecimal() and int(expected) <= maximum,
                "comic_response_too_large",
                503,
            )
            expected = int(expected)
            if destination is None:
                raw = response.read(maximum + 1)
                require(len(raw) == expected, "comic_input_service_invalid_response", 503)
                value = json.loads(raw)
                require(isinstance(value, dict), "comic_input_service_invalid_response", 503)
                return value
            require(expected == body["file"]["size"], "comic_input_changed", 409)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name("." + destination.name + "." + uuid.uuid4().hex)
            count = 0
            with temporary.open("xb") as output:
                while chunk := response.read(65536):
                    count += len(chunk)
                    require(count <= maximum and count <= expected, "comic_input_changed", 409)
                    output.write(chunk)
                require(count == expected, "comic_input_changed", 409)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            sync_directory(destination.parent)
            return destination
        except (http.client.HTTPException, OSError):
            raise Fault("comic_input_service_unavailable", 503) from None
        except (ValueError, UnicodeError):
            raise Fault("comic_input_service_invalid_response", 503) from None
        finally:
            connection.close()
            if temporary:
                temporary.unlink(missing_ok=True)

    def probe(self):
        value = self.request("probe", {})
        require(
            value.get("status") == "alive" and value.get("protocol") == PROTOCOL,
            "comic_input_service_invalid_response",
            503,
        )
        return value

    def browse(self, relative="", offset=0):
        return self.request("browse", {"relative_path": relative, "offset": offset})

    def inspect(self, relative):
        return self.request("inspect", {"relative_path": relative})

    def recheck(self, chapter):
        return self.request(
            "recheck",
            {"chapter": {k: chapter[k] for k in ("relative_path", "input_kind", "input_digest")}},
        )

    def copy_file(self, relative, record, destination, maximum=MAX_CHAPTER_BYTES + 16 * 1024**2):
        return self.request(
            "file",
            {"relative_path": relative, "file": record},
            destination=destination,
            maximum=maximum,
        )
