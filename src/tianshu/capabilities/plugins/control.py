"""Control-plane request schemas and OpenAPI generated from the same operation registry."""

from ..contracts import require
from .schema import check

TEXT = {"type": "string", "minLength": 1, "maxLength": 128}
REVISION = {"type": "integer", "minimum": 1}
OBJECT = {"type": "object"}


def request(properties=None, optional=()):
    properties = properties or {}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": [name for name in properties if name not in optional],
    }


INSTANCE = {"instance_id": TEXT, "expected_revision": REVISION}
OPERATIONS = {
    "view": request(),
    "catalog": request(),
    "actors": request(),
    "schema": request(),
    "install": request(
        {
            "source": {
                "oneOf": [
                    request({"type": {"const": "builtin"}, "id": TEXT}),
                    request({"type": {"const": "artifact"}, "media": OBJECT}),
                    request(
                        {"type": {"const": "directory"}, "path": {"type": "string", "minLength": 1}}
                    ),
                    request(
                        {
                            "type": {"const": "git"},
                            "url": {"type": "string", "format": "uri"},
                            "commit": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                        }
                    ),
                    request(
                        {"type": {"const": "zip"}, "data": {"type": "string", "maxLength": 8388608}}
                    ),
                ]
            },
            "client_request_id": TEXT,
        }
    ),
    "installation": request({"id": TEXT}),
    "create": request({"package_id": TEXT, "name": TEXT, "client_request_id": TEXT}),
    "configure": request(
        {
            **INSTANCE,
            "config": OBJECT,
            "secrets": {"type": "object", "additionalProperties": {"type": ["string", "null"]}},
        },
        optional=("secrets",),
    ),
    "enable": request(INSTANCE),
    "disable": request(INSTANCE),
    "bindings": request({**INSTANCE, "bindings": {"type": "array", "maxItems": 128}}),
    "invoke": request(
        {
            "instance_id": TEXT,
            "action": TEXT,
            "input": OBJECT,
            "client_request_id": TEXT,
            "binding_id": TEXT,
        },
        optional=("binding_id",),
    ),
    "invocations": request({"instance_id": TEXT}, optional=("instance_id",)),
    "invocation": request({"id": TEXT}),
    "cancel": request({"id": TEXT}),
    "reconcile": request({"id": TEXT}),
    "resume": request({"id": TEXT, "client_request_id": TEXT}),
    "upgrade": request({**INSTANCE, "version": TEXT}),
    "reload": request(INSTANCE),
    "uninstall": request(
        {
            "package_id": TEXT,
            "purge_data": {"type": "boolean", "default": False},
        },
        optional=("purge_data",),
    ),
    "logs": request({"instance_id": TEXT}),
    "view-content": request(
        {"instance_id": TEXT, "page_id": TEXT, "nonce": {"type": "string", "format": "uuid"}}
    ),
    "schedules": {
        "oneOf": [
            request({"instance_id": TEXT}),
            request(
                {
                    "instance_id": TEXT,
                    "operation": {"enum": ["schedule.put", "schedule.remove"]},
                    "value": OBJECT,
                }
            ),
        ]
    },
}


def validate(operation, body):
    require(operation in OPERATIONS, "not_found", 404)
    check(OPERATIONS[operation], body)


def openapi():
    from .port_schemas import describe

    document = {
        "openapi": "3.1.0",
        "info": {"title": "TianShu Plugin Control", "version": "1.0.0"},
        "x-host-capabilities": describe(),
        "paths": {
            "/api/v1/plugins/" + operation: {
                "post": {
                    "operationId": "plugins_" + operation.replace("-", "_"),
                    "security": [{"session": [], "csrf": []}],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": schema}},
                    },
                    "responses": {
                        "200": {
                            "description": "Operation result; invocation execution/effect/external/delivery are separate."
                        },
                        "400": {"description": "Invalid input"},
                        "401": {"description": "Session expired"},
                        "403": {"description": "Forbidden"},
                        "409": {"description": "Revision, intent or capability conflict"},
                    },
                }
            }
            for operation, schema in OPERATIONS.items()
        },
        "components": {
            "securitySchemes": {
                "session": {"type": "apiKey", "in": "cookie", "name": "tianshu_session"},
                "csrf": {"type": "apiKey", "in": "header", "name": "X-CSRF-Token"},
            }
        },
    }
    document["paths"]["/api/v1/plugins/page/{ticket}"] = {
        "get": {
            "operationId": "plugins_page",
            "security": [{"session": []}],
            "parameters": [{"in": "path", "name": "ticket", "required": True, "schema": TEXT}],
            "responses": {
                "200": {
                    "description": "Single-use sandboxed plugin page",
                    "content": {"text/html": {"schema": {"type": "string"}}},
                },
                "401": {"description": "Session expired"},
                "404": {"description": "Page ticket expired or consumed"},
            },
        }
    }
    return document
