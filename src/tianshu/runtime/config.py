"""One explicit product configuration and secret references, shared by all process roles."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
from importlib.resources import files
import re

from jsonschema import Draft202012Validator

from .database import DatabaseConfig, resource_budget
from .errors import AppError
from .execution import PROCESS_ROLES, lane_concurrency

ROLES = PROCESS_ROLES


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
        if role not in ROLES:
            raise ValueError("invalid_process_role")
        value = self.document["database"]
        budgets = value.get("roles", {})
        role_budget = budgets.get(role, {})
        budget = resource_budget(self.document, self.enabled_roles)
        if budget["postgres_connections"] > budget["postgres_limit"]:
            raise ValueError("database_connection_budget_exceeded")
        return DatabaseConfig(
            self.secrets.resolve(value.get("dsn_ref", "TIANSHU_DATABASE_URL")),
            max_connections=role_budget.get("max_connections", 4),
            control_connections=role_budget.get("control_connections", 2),
            sync_connections=role_budget.get("sync_connections", 2),
            connect_timeout=value.get("connect_timeout", 5),
        )

    @property
    def worker_lanes(self):
        return lane_concurrency(self.document.get("worker", {}))

    @property
    def enabled_roles(self):
        return tuple(self.document.get("supervisor", {}).get("roles", ROLES))

    @property
    def model_transport(self):
        return self.model_transport_for("interactive")

    def model_transport_for(self, role):
        configured = self.document.get("broker", {}).get("transport", {})
        configured = {**configured, **configured.get("roles", {}).get(role, {})}
        return {
            "max_connections": configured.get("foreground_connections", 8),
            "due_connections": configured.get("due_connections", 4),
            "background_connections": configured.get("background_connections", 24),
            "control_connections": configured.get("control_connections", 4),
        }

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

    @property
    def ui_release_directory(self):
        http = self.document.get("http", {})
        if "ui_release_directory" in http:
            configured = http["ui_release_directory"]
            if configured is None:
                return None
            path = Path(configured)
            return path if path.is_absolute() else self.base_directory / path
        if "artifacts" in self.document:
            return self.artifact_directory.parent / "ui"
        return None
