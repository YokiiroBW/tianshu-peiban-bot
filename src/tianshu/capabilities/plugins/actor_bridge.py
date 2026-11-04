"""Narrow role projection over the plugin instance, binding and execution owners."""

import copy

from jsonschema import Draft202012Validator, SchemaError

from ..contracts import Fault, require
from .schema import merge, validate_config


def local_schema(value):
    """Role tools may resolve fragments in their own schema, never fetch another resource."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"$ref", "$dynamicRef"} and (
                not isinstance(item, str) or not item.startswith("#")
            ):
                return False
            if key == "$id":
                return False
            if not local_schema(item):
                return False
    elif isinstance(value, list):
        return all(local_schema(item) for item in value)
    return True


def binding_visible(context, binding):
    """Character tools use their role; independent tools use a granted workspace scope."""
    scope = binding.get("scope", {})
    if scope.get("kind") == "character":
        return context.actor_id is not None and scope.get("character_id") == context.actor_id
    if scope.get("kind") == "workspace":
        return context.actor_id is None and scope.get("workspace_id") in context.scope_ids
    return False


class PluginActorBridge:
    def __init__(self, host):
        self.host, self.repo = host, host.repository

    def resolve(self, context, reference, *, exact=True):
        instance = self.repo.get("instances", reference["instance_id"])
        require(instance is not None, "not_found", 404)
        binding = next(
            (row for row in instance["bindings"] if row["binding_id"] == reference["binding_id"]),
            None,
        )
        require(
            binding
            and binding["enabled"]
            and binding_visible(context, binding)
            and {"kind": "action", "id": reference["action_id"]} in binding.get("entries", []),
            "forbidden",
            403,
        )
        if exact:
            require(instance["revision"] == reference["binding_revision"], "version_conflict", 409)
        package = self.repo.get("packages", instance["package_id"] + "@" + instance["version"])
        require(package and package["installed"], "capability_unavailable", 409)
        action = next(
            (
                row
                for row in package["description"]["actions"]
                if row["id"] == reference["action_id"]
            ),
            None,
        )
        require(action is not None, "capability_unavailable", 409)
        return instance, binding, package, action

    def ready(self, instance, binding, package, action):
        require(instance["desired_state"] == "enabled", "capability_unavailable", 409)
        require(
            all(
                item["id"] in self.host.available_capabilities and item["major"] == 1
                for item in package["manifest"]["capabilities"]["required"]
                + action["required_capabilities"]
            ),
            "capability_unavailable",
            409,
        )
        require(
            local_schema(action["input_schema"]) and local_schema(action["output_schema"]),
            "capability_unavailable",
            409,
        )
        try:
            Draft202012Validator.check_schema(action["input_schema"])
            Draft202012Validator.check_schema(action["output_schema"])
            validate_config(
                package["schema"],
                merge(instance["config"], binding["overrides"]),
                self.host.vault.names(instance["id"]),
            )
        except (SchemaError, Fault):
            raise Fault("capability_unavailable", 409) from None

    @staticmethod
    def reference(instance, binding, action):
        return {
            "binding_id": binding["binding_id"],
            "binding_revision": instance["revision"],
            "instance_id": instance["id"],
            "action_id": action["id"],
        }

    async def discover(self, context):
        capabilities = []
        for instance in await self.host.local(self.repo.list, "instances"):
            # The richer UI projection may probe external comic file workers.
            # Declarations only need locally installed package metadata.
            try:
                selected = [row for row in instance["bindings"]
                            if row["enabled"] and binding_visible(context, row)]
                if not selected:
                    continue
                package = await self.host.package(instance["package_id"], instance["version"])
                description = package["description"]
                actions = description["actions"]
                if not isinstance(actions, list):
                    continue
            except (Fault, KeyError, TypeError):
                continue
            for binding in selected:
                for action in actions:
                    try:
                        if {"kind": "action", "id": action["id"]} not in binding.get("entries", []):
                            continue
                        reference = self.reference(instance, binding, action)
                        entry = {
                            "binding": reference,
                            "scope": binding["scope"],
                            "model_tool":next((tool for tool in description.get("tools",[]) if tool["action"]==action["id"]),None),
                            "public_command":next(({"name":command["names"][0],"aliases":command["names"][1:]} for command in description.get("commands",[]) if command["action"]==action["id"]),None),
                            "title": action["title"],
                            "description": action.get("description", ""),
                            "effect": action["effect"],
                            "long_running":action["timeout_seconds"]>30,
                            "input_schema": copy.deepcopy(action["input_schema"]),
                            "output_schema": copy.deepcopy(action["output_schema"]),
                            "availability": "not_verified",
                            "reason": None,
                        }
                    except (KeyError, TypeError, IndexError):
                        continue
                    capabilities.append(entry)
                    require(len(capabilities) <= 128, "capacity_exceeded", 413)
        return capabilities
