"""One explicit product configuration and secret references, shared by all process roles."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
from importlib.resources import files
import re

from jsonschema import Draft202012Validator

from .database import DatabaseConfig
from .errors import AppError

ROLES = ("interactive", "model-broker", "worker")


class Secrets:
    def __init__(self, environ=None):
        self.environ = os.environ if environ is None else environ

    def resolve(self, reference):
        name = reference.removeprefix("env:")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise AppError("invalid_credential_reference", status=503)
        value = self.environ.get(name)
        if not value:
            raise AppError("credential_not_configured", status=503)
        return value


@dataclass(frozen=True, slots=True)
class Settings:
    document: dict
    secrets: Secrets
    base_directory: Path

    def get(self, name, default=None):
        return self.document.get(name, default)

    def __getitem__(self, name):
        return self.document[name]

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("configuration_object_required")
        schema = json.loads(files("tianshu.runtime").joinpath("config.schema.json").read_text())
        errors = sorted(
            Draft202012Validator(schema).iter_errors(document),
            key=lambda error: list(map(str, error.path)),
        )
        if errors:
            # A schema error can contain secret values; expose only its location and keyword.
            error = errors[0]
            raise ValueError(
                "invalid_configuration:"
                + ".".join(map(str, error.path))
                + ":"
                + str(error.validator)
            )
        return cls(document, Secrets(), path.parent)

    def database(self, role):
        value = self.document["database"]
        budgets = value.get("roles", {})
        role_budget = budgets.get(role, {})
        total = sum(
            item.get("max_connections", 4)
            + item.get("control_connections", 2)
            + item.get("sync_connections", 2)
            for item in (budgets.get(name, {}) for name in ROLES)
        )
        # Procrastinate 3.10 creates one standalone LISTEN connection per consumer,
        # outside its configured shared pool. The broker advisory owner is separate too.
        total += self.document.get("worker", {}).get("queue_connections", 8) + 3 + 1
        if total > value.get("connection_budget", 40):
            raise ValueError("database_connection_budget_exceeded")
        return DatabaseConfig(
            self.secrets.resolve(value.get("dsn_ref", "TIANSHU_DATABASE_URL")),
            max_connections=role_budget.get("max_connections", 4),
            control_connections=role_budget.get("control_connections", 2),
            sync_connections=role_budget.get("sync_connections", 2),
            connect_timeout=value.get("connect_timeout", 5),
        )

    @property
    def broker_url(self):
        return self.document.get("broker", {}).get("url", "http://127.0.0.1:8071")

    @property
    def broker_token(self):
        return self.secrets.resolve(
            self.document.get("broker", {}).get("token_ref", "TIANSHU_BROKER_TOKEN")
        )

    @property
    def artifact_directory(self):
        path = Path(self.document.get("artifacts", "data/artifacts"))
        return path if path.is_absolute() else self.base_directory / path
