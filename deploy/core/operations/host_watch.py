#!/usr/bin/env python3
"""Independent, explicitly configured host watch; uses only fixed Docker subcommands.

The installer supplies names, deployment generation, spool path and SMTP. This
program does not read product databases or accept commands from a model.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from email.message import EmailMessage
import hashlib
import json
import os
from pathlib import Path
import re
import smtplib
import socket
import ssl
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
from urllib.error import HTTPError


NAME = re.compile(r"^[a-zA-Z0-9_.:/-]{1,200}$")
ROLES = {"interactive", "model-broker", "bot-foreground", "bot-background", "independent", "operations"}


def digest(parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    with tmp.open("w", encoding="utf-8") as stream:
        os.chmod(tmp, 0o600)
        json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)
    if hasattr(os, "O_DIRECTORY"):
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def read_config(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value.get("spool_directory"), str) or not Path(value["spool_directory"]).is_absolute():
        raise ValueError("absolute_spool_directory_required")
    targets = value.get("targets")
    if not isinstance(targets, list):
        raise ValueError("targets_required")
    for item in targets:
        if (not isinstance(item, dict) or item.get("role") not in ROLES
                or not NAME.fullmatch(item.get("container", ""))
                or not NAME.fullmatch(item.get("generation", ""))
                or type(item.get("restart_on_stopped", False)) is not bool):
            raise ValueError("invalid_registered_target")
    smtp = value.get("smtp")
    if smtp is not None:
        if (not isinstance(smtp, dict) or not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", smtp.get("host", ""))
                or type(smtp.get("port")) is not int or not 1 <= smtp["port"] <= 65535
                or smtp.get("tls") not in {"tls", "starttls"}):
            raise ValueError("invalid_smtp")
        for field in ("sender", "recipient"):
            if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+", smtp.get(field, "")):
                raise ValueError("invalid_smtp")
        ref = smtp.get("password_env")
        if ref is not None and not re.fullmatch(r"TIANSHU_OPS_SMTP_[A-Z0-9_]+", ref):
            raise ValueError("invalid_smtp_password_reference")
    db = value.get("database_probe")
    if db is not None and (not isinstance(db, dict) or not re.fullmatch(r"[a-zA-Z0-9.:-]{1,253}", db.get("host", ""))
            or type(db.get("port")) is not int or not 1 <= db["port"] <= 65535
            or not NAME.fullmatch(db.get("generation", ""))):
        raise ValueError("invalid_database_probe")
    if value.get("heartbeat_url"):
        url = urlsplit(value["heartbeat_url"])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("invalid_heartbeat_url")
    return value


def inspect(container):
    try:
        result = subprocess.run(["docker", "inspect", container], capture_output=True, text=True, timeout=10, check=False)
        if result.returncode:
            return {"state": "missing" if "No such" in result.stderr else "probe_unavailable"}
        data = json.loads(result.stdout)[0]
        return {"state": data["State"]["Status"], "id": data["Id"], "labels": data["Config"].get("Labels") or {}}
    except (OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired):
        return {"state": "probe_unavailable"}


def registered(target, actual):
    labels = actual.get("labels", {})
    return (labels.get("com.tianshu.product") == "tianshu"
            and labels.get("com.tianshu.role") == target["role"]
            and labels.get("com.tianshu.generation") == target["generation"])


def smtp_send(config, event, incident_id):
    notice = "notice_" + digest([incident_id, "opened", "smtp", config["recipient"], event["event_id"]])
    message = EmailMessage()
    message["From"], message["To"] = config["sender"], config["recipient"]
    message["Subject"] = "[天枢运维] " + event["error_family"] + " " + event["dependency"]
    message["Message-ID"] = "<" + notice + "@tianshu.local>"
    message.set_content(json.dumps(event, ensure_ascii=False, indent=2))
    client, submitted = None, False
    try:
        context = ssl.create_default_context()
        client = (smtplib.SMTP_SSL(config["host"], config["port"], timeout=15, context=context)
                  if config["tls"] == "tls" else smtplib.SMTP(config["host"], config["port"], timeout=15))
        if config["tls"] == "starttls":
            client.starttls(context=context)
        if config.get("username"):
            client.login(config["username"], os.environ.get(config.get("password_env", ""), ""))
        submitted = True
        refused = client.send_message(message)
        return {"state": "failed", "code": "smtp_recipient_refused"} if refused else {
            "state": "accepted", "meaning": "server_accepted_not_read"}
    except smtplib.SMTPResponseException as exc:
        return {"state": "failed", "code": "smtp_" + str(exc.smtp_code)}
    except Exception as exc:
        return {"state": "unknown" if submitted else "failed", "code": type(exc).__name__}
    finally:
        if client:
            client.close()


def event_for(target, actual, sequence):
    family = ("container_probe_unavailable" if actual["state"] == "probe_unavailable" else
              "container_not_found" if actual["state"] == "missing" else
              "container_owner_mismatch" if not registered(target, actual) else "role_stopped")
    dependency = "host:" + target["container"]
    version = target["generation"]
    incident_id = "incident_" + digest([dependency, family, version])
    return {"event_id": "host:" + digest([dependency, family, version, sequence]),
            "dependency": dependency, "error_family": family, "version": version,
            "affected": [target["role"]], "role": target["role"], "severity": "critical",
            "status": actual["state"], "event": "host.container", "incident_id": incident_id}


def record_failure(spool, config, state, key, event_factory):
    previous = state.setdefault(key, {"fault": False, "sequence": 0, "restarts": 0, "event_recorded": False})
    sample = event_factory(previous["sequence"])
    signature = digest([sample["dependency"], sample["error_family"], sample["version"]])
    if not previous["fault"] or previous.get("signature") != signature:
        previous.update(fault=True, sequence=previous["sequence"]+1, event_recorded=False)
        previous["signature"] = signature
    event = event_factory(previous["sequence"])
    path = spool / ("event-" + digest([event["event_id"]]) + ".json")
    if not previous.get("event_recorded"):
        if not path.exists():
            atomic_json(path, {"event": event, "notification": {"state": "pending", "attempts": 0,
                "updated_at": datetime.now(UTC).isoformat(), "target": config["smtp"]["recipient"]}
                if config.get("smtp") else None})
        previous["event_recorded"] = True
        atomic_json(spool / "watch-state.json", state)
    return previous


def simple_event(dependency, family, version, role, sequence):
    incident_id = "incident_" + digest([dependency, family, version])
    return {"event_id": "host:" + digest([dependency, family, version, sequence]), "dependency": dependency,
            "error_family": family, "version": version, "affected": [role], "role": role,
            "severity": "error", "event": "host.probe", "incident_id": incident_id}


def send_pending(path, data, smtp):
    if not smtp:
        return
    notice = data.setdefault("notification", {"state": "pending", "attempts": 0, "target": smtp["recipient"]})
    if notice["state"] == "sending":
        # Process loss after SMTP DATA may have delivered the previous message.
        notice.update(state="unknown", code="host_sender_lost")
        atomic_json(path, data)
        return
    if notice["state"] not in {"pending", "failed"} or notice["attempts"] >= 5:
        return
    notice.update(state="sending", attempts=notice["attempts"]+1, updated_at=datetime.now(UTC).isoformat())
    atomic_json(path, data)
    notice.update(smtp_send(smtp, data["event"], data["event"]["incident_id"]))
    notice["updated_at"] = datetime.now(UTC).isoformat()
    atomic_json(path, data)


def tick(config, state):
    spool = Path(config["spool_directory"])
    spool.mkdir(parents=True, exist_ok=True, mode=0o700)
    for target in config["targets"]:
        key = target["role"] + ":" + target["container"] + ":" + target["generation"]
        previous = state.setdefault(key, {"fault": False, "sequence": 0, "restarts": 0})
        actual = inspect(target["container"])
        healthy = actual["state"] == "running" and registered(target, actual)
        if healthy:
            # Process availability is recorded only; business recovery still
            # needs the domain's authoritative observation.
            previous["fault"] = False
            continue
        previous = record_failure(spool, config, state, key, lambda sequence: event_for(target, actual, sequence))
        if (target.get("restart_on_stopped") and actual["state"] in {"exited", "created"}
                and registered(target, actual) and previous["restarts"] < 3):
            # Reinspect immediately before a finite start; only this exact
            # product role, container ID and deployment generation qualify.
            latest = inspect(target["container"])
            if latest.get("id") == actual.get("id") and latest["state"] in {"exited", "created"} and registered(target, latest):
                previous["restarts"] += 1
                atomic_json(spool / "watch-state.json", state)
                try:
                    subprocess.run(["docker", "start", actual["id"]], timeout=30, capture_output=True, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    pass
    db = config.get("database_probe")
    if db:
        key = "database-tcp:" + db["host"] + ":" + str(db["port"]) + ":" + db["generation"]
        try:
            with socket.create_connection((db["host"], db["port"]), timeout=5):
                available = True
        except OSError:
            available = False
        if available:
            state.setdefault(key, {"fault": False, "sequence": 0})["fault"] = False
        else:
            record_failure(spool, config, state, key, lambda sequence: simple_event(
                "database", "database_tcp_unreachable", db["generation"], "operations", sequence))
    if config.get("heartbeat_url"):
        key = "heartbeat:" + config["heartbeat_url"]
        try:
            try:
                response = urlopen(config["heartbeat_url"], timeout=5)
            except HTTPError as exc:
                if exc.code != 503:
                    raise
                response = exc
            with response:
                health = json.load(response)
        except Exception:
            health = None
        if health is None:
            record_failure(spool, config, state, key, lambda sequence: simple_event(
                "interactive", "heartbeat_endpoint_unavailable", "unknown", "interactive", sequence))
        else:
            state.setdefault(key, {"fault": False, "sequence": 0})["fault"] = False
            for role in ROLES:
                role_key = key + ":" + role
                if role in health.get("missing_roles", ()):
                    record_failure(spool, config, state, role_key, lambda sequence, role=role: simple_event(
                        "role:" + role, "role_heartbeat_missing", "unknown", role, sequence))
                else:
                    state.setdefault(role_key, {"fault": False, "sequence": 0})["fault"] = False
    atomic_json(spool / "watch-state.json", state)
    for path in sorted(spool.glob("event-*.json")):
        try:
            import fcntl
            with path.with_name(path.name + ".lock").open("a+b") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    send_pending(path, data, config.get("smtp"))
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        except (OSError, ValueError, TypeError, KeyError):
            continue


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = read_config(args.config)
    spool = Path(config["spool_directory"])
    try:
        state = json.loads((spool / "watch-state.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {}
    while True:
        tick(config, state)
        time.sleep(30)


if __name__ == "__main__":
    main()
